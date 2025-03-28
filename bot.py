import telebot
from telebot import TeleBot
from telebot.types import Message # הוספת ייבוא מפורש
import time
from datetime import datetime
import json
import os
import logging
import threading
from typing import Set, Dict, Any, List, Optional # הוספת ייבוא ל-Type Hinting

# --- הגדרות לוגר ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log", encoding='utf-8'), # שמירת לוגים לקובץ
        logging.StreamHandler() # הדפסה גם למסך
    ]
)
logger = logging.getLogger(__name__)

# --- קבועים ---
# !! חשוב: הגדר את משתנה הסביבה TELEGRAM_BOT_TOKEN עם הטוקן שלך !!
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
if not BOT_TOKEN:
    logger.critical("שגיאה קריטית: משתנה הסביבה TELEGRAM_BOT_TOKEN אינו מוגדר.")
    exit("משתנה הסביבה TELEGRAM_BOT_TOKEN אינו מוגדר.")

WRITING_USERS_FILE = 'writing_users.json'
ALL_SEEN_USERS_FILE = 'all_seen_users.json'
USER_DETAILS_FILE = 'user_details.json'
ADMIN_IDS_FILE = 'admin_ids.json'

# --- מנעול לגישה בטוחה לנתונים משותפים ---
data_lock = threading.Lock()

# --- פונקציות עזר לטעינה ושמירת נתונים ---
def load_data(filename: str, default_value: Any) -> Any:
    """טוען נתונים מקובץ JSON, מחזיר ערך ברירת מחדל אם הקובץ לא קיים או פגום."""
    if os.path.exists(filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read()
                if not content:
                    logger.warning(f"קובץ {filename} ריק, משתמש בערך ברירת מחדל.")
                    return default_value
                data = json.loads(content)
                # המרה חזרה ל-set אם צריך
                if isinstance(default_value, set):
                    return set(data)
                # המרה של מפתחות USER_DETAILS חזרה ל-int
                elif filename == USER_DETAILS_FILE and isinstance(data, dict):
                     # ודא שהמפתחות אכן מחרוזות לפני ההמרה
                    return {int(k): v for k, v in data.items() if isinstance(k, str) and k.isdigit()}
                return data
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"שגיאה בטעינת {filename}: {e}. משתמש בערך ברירת מחדל.", exc_info=True)
            return default_value
        except Exception as e:
            logger.error(f"שגיאה לא צפויה בטעינת {filename}: {e}. משתמש בערך ברירת מחדל.", exc_info=True)
            return default_value
    logger.info(f"קובץ {filename} לא נמצא, יוצר עם ערך ברירת מחדל.")
    return default_value

def save_data(filename: str, data: Any) -> None:
    """שומר נתונים לקובץ JSON בצורה בטוחה."""
    try:
        # הכתיבה לקובץ עצמה צריכה להיות מוגנת אם היא נקראת מכמה threads
        # מאחר והיא נקראת רק מפונקציות שמוגנות כבר ע"י data_lock, אין צורך במנעול נוסף כאן.
        with open(filename, 'w', encoding='utf-8') as f:
            if isinstance(data, set):
                json.dump(list(data), f, indent=4, ensure_ascii=False)
            else:
                json.dump(data, f, indent=4, ensure_ascii=False)
        logger.debug(f"נתונים נשמרו בהצלחה לקובץ {filename}")
    except IOError as e:
        logger.error(f"שגיאה בשמירת {filename}: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"שגיאה לא צפויה בשמירת {filename}: {e}", exc_info=True)

# --- טעינת נתונים גלובליים ---
logger.info("טוען נתונים קיימים...")
# אין צורך במנעול כאן כי זה קורה פעם אחת בהתחלה לפני שה-threads של הבוט מתחילים
writing_users: Set[int] = load_data(WRITING_USERS_FILE, set())
all_seen_users: Set[int] = load_data(ALL_SEEN_USERS_FILE, set())
user_details: Dict[int, Dict[str, str]] = load_data(USER_DETAILS_FILE, {})
admin_ids: Set[int] = load_data(ADMIN_IDS_FILE, set())
logger.info(f"טעינה הושלמה: {len(all_seen_users)} נראו, {len(writing_users)} כתבו, {len(user_details)} פרטים נשמרו, {len(admin_ids)} מנהלים.")

# --- אתחול הבוט ---
bot = TeleBot(BOT_TOKEN)
logger.info("הבוט מאותחל...")

# --- פונקציות ליבה ---
def update_user_info(user: telebot.types.User) -> None:
    """מעדכן את פרטי המשתמש בקבצים ובזיכרון, תוך שימוש במנעול."""
    user_id = user.id
    is_newly_seen = False
    details_changed = False

    with data_lock: # הגנה על גישה ושינוי של all_seen_users ו-user_details
        # עדכון רשימת כל הנצפים
        if user_id not in all_seen_users:
            all_seen_users.add(user_id)
            save_data(ALL_SEEN_USERS_FILE, all_seen_users)
            is_newly_seen = True
            logger.info(f"משתמש חדש נצפה: ID={user_id}")

        # עדכון פרטי משתמש (שם, יוזר, שפה)
        current_details = user_details.get(user_id, {})
        new_first_name = user.first_name or ""
        new_username = user.username or ""
        new_language_code = user.language_code or "N/A" # קוד שפה, עם ערך חלופי

        # בדוק אם יש שינוי לפני כתיבה לקובץ
        if (current_details.get('first_name') != new_first_name or
            current_details.get('username') != new_username or
            current_details.get('language_code') != new_language_code or
            (is_newly_seen and 'join_date' not in current_details)): # שמירת תאריך רק אם חדש

            join_date = current_details.get('join_date', datetime.now().isoformat()) # שמור תאריך קיים אם יש
            user_details[user_id] = {
                'first_name': new_first_name,
                'username': new_username,
                'language_code': new_language_code,
                'join_date': join_date # תאריך הצטרפות ראשוני נשמר
            }
            save_data(USER_DETAILS_FILE, user_details)
            details_changed = True
            logger.info(f"פרטי משתמש עודכנו/נוספו: ID={user_id}")

    # הדפסות ללוג יכולות להיות מחוץ למנעול כדי לא להחזיק אותו יותר מדי זמן
    if is_newly_seen:
        logger.info(f"פרטי משתמש חדש: {new_first_name} (@{new_username}) שפה: {new_language_code} (ID: {user_id})")
    elif details_changed:
        logger.info(f"פרטי משתמש עודכנו: {new_first_name} (@{new_username}) (ID: {user_id})")

def admin_only(func):
    """דקורטור לבדיקה אם המשתמש הוא מנהל ושההודעה נשלחה בפרטי."""
    def wrapper(message: Message, *args, **kwargs):
        user_id = message.from_user.id
        # ודא שה-admin_ids נבדקים בצורה בטוחה (לקריאה זה פחות קריטי ממנעול מלא, אבל עדיף להיות זהיר)
        with data_lock:
            is_admin = user_id in admin_ids

        if message.chat.type != 'private':
             bot.reply_to(message, "פקודה זו זמינה רק בצ'אט פרטי עם הבוט.")
             logger.warning(f"ניסיון שימוש בפקודה {message.text.split()[0]} שלא בפרטי ע توسط ID={user_id}")
             return
        if not is_admin:
            bot.reply_to(message, "רק מנהלים רשומים רשאים להשתמש בפקודה זו.")
            logger.warning(f"ניסיון לא מורשה להשתמש בפקודה {message.text.split()[0]} ע توسط ID={user_id}")
            return
        return func(message, *args, **kwargs)
    return wrapper

# --- Message Handlers ---

@bot.message_handler(commands=['start'])
def send_welcome(message: Message):
    """שולח הודעת ברוכים הבאים."""
    if message.chat.type == 'private':
        user = message.from_user
        logger.info(f"משתמש התחיל צ'אט: {user.first_name} (@{user.username}) ID={user.id}")
        update_user_info(user) # עדכן מידע על המשתמש אם הוא חדש
        bot.reply_to(message, "ברוכים הבאים! אני בוט שעוקב אחר פעילות משתמשים בקבוצה.\n"
                              "כדי לקבל הרשאות ניהול, בקש ממנהל קיים להוסיף אותך, או אם אתה הראשון, השתמש ב- /set_admin פעם אחת.")
    # אין צורך ב-else, הבוט לא אמור להגיב ל-start בקבוצות

@bot.message_handler(commands=['set_admin'])
def set_admin(message: Message):
    """מגדיר את שולח ההודעה כמנהל (רק בפרטי)."""
    if message.chat.type == 'private':
        user_id = message.from_user.id
        with data_lock: # הגנה על גישה וכתיבה ל-admin_ids
            if user_id not in admin_ids:
                admin_ids.add(user_id)
                save_data(ADMIN_IDS_FILE, admin_ids)
                logger.info(f"משתמש ID={user_id} הוגדר כמנהל.")
                bot.reply_to(message, "הוגדרת בהצלחה כמנהל. כעת תוכל להשתמש בפקודות /stats ו- /check_inactive.")
            else:
                logger.info(f"משתמש ID={user_id} ניסה להגדיר את עצמו כמנהל, אך כבר מנהל.")
                bot.reply_to(message, "אתה כבר מוגדר כמנהל.")
    else:
        # אין תגובה אם הפקודה נשלחת בקבוצה
        logger.warning(f"ניסיון להשתמש ב-/set_admin בקבוצה על ידי ID={message.from_user.id}")


@bot.message_handler(commands=['stats'])
@admin_only
def show_bot_stats(message: Message):
    """מציג סטטיסטיקות בוט (רק למנהלים בפרטי)."""
    logger.info(f"מנהל ID={message.from_user.id} ביקש סטטיסטיקות.")
    try:
        with data_lock: # קריאה בטוחה של הנתונים
            # עבודה על עותקים למקרה שהנתונים ישתנו בזמן חישוב השפות
            current_all_seen_count = len(all_seen_users)
            current_writing_count = len(writing_users)
            details_copy = user_details.copy() # עותק של פרטי המשתמש

        # חישוב שפות מחוץ למנעול
        unique_languages = set(details.get('language_code', 'N/A') for details in details_copy.values())
        # הסר 'N/A' אם לא רוצים לספור אותו כשפה
        # unique_languages.discard('N/A')
        num_unique_languages = len(unique_languages)

        stats_message = f"""
📊 סטטיסטיקת בוט:
👥 סה"כ משתמשים שנצפו: {current_all_seen_count}
✍️ משתמשים שכתבו הודעה אחת לפחות: {current_writing_count}
🌍 מספר שפות ייחודיות שנרשמו: {num_unique_languages}
    """.strip()

        bot.reply_to(message, stats_message)

    except Exception as e:
        logger.error(f"שגיאה ביצירת סטטיסטיקות: {e}", exc_info=True)
        bot.reply_to(message, "אירעה שגיאה בעת הכנת הסטטיסטיקות.")


@bot.message_handler(content_types=['new_chat_members'])
def handle_new_member(message: Message):
    """מעדכן פרטים של משתמשים חדשים שמצטרפים לקבוצה."""
    try:
        for user in message.new_chat_members:
            if not user.is_bot:
                logger.info(f"חבר חדש הצטרף לקבוצה ID={message.chat.id}: {user.first_name} (@{user.username}) ID={user.id}")
                update_user_info(user) # הפונקציה הזו כבר מוגנת במנעול
    except Exception as e:
        logger.error(f"שגיאה בטיפול בחבר חדש: {e}", exc_info=True)

# רשימת סוגי תוכן המעידים על כתיבה
WRITING_CONTENT_TYPES = ['text', 'audio', 'voice', 'video', 'document', 'photo', 'sticker', 'contact', 'location', 'venue', 'video_note', 'poll']

@bot.message_handler(func=lambda message: message.from_user is not None and not message.from_user.is_bot, content_types=WRITING_CONTENT_TYPES)
def track_activity(message: Message):
    """עוקב אחר משתמשים שכותבים הודעה ומוסיף אותם לרשימת הכותבים."""
    try:
        user = message.from_user
        user_id = user.id

        # עדכן את פרטי המשתמש (אם השתנו או אם הוא נצפה לראשונה כאן)
        update_user_info(user) # הפונקציה הזו כבר מוגנת במנעול

        # הוסף לרשימת הכותבים רק אם לא נמצא שם כבר
        with data_lock: # הגנה על גישה וכתיבה ל-writing_users
            if user_id not in writing_users:
                writing_users.add(user_id)
                save_data(WRITING_USERS_FILE, writing_users)
                logger.info(f"משתמש כתב לראשונה: {user.first_name} (@{user.username}) ID={user_id}")
                # אין צורך להדפיס ללוג בכל הודעה, רק בפעם הראשונה
            # else: # אפשר להוסיף לוג ברמת DEBUG אם רוצים לעקוב אחר כל הודעה
            #    logger.debug(f"משתמש ID={user_id} שלח הודעה מסוג {message.content_type}")

    except AttributeError as ae:
         # יכול לקרות אם ההודעה מגיעה ממקור לא צפוי ללא from_user
         logger.warning(f"הודעה ללא 'from_user' התקבלה: {ae}", exc_info=False)
    except Exception as e:
        logger.error(f"שגיאה במעקב אחר פעילות: {e}", exc_info=True)


@bot.message_handler(commands=['check_inactive'])
@admin_only
def check_inactive_users(message: Message):
    """שולח למנהל רשימה של משתמשים שנצפו אך לא כתבו (רק בפרטי)."""
    user_id = message.from_user.id
    logger.info(f"מנהל ID={user_id} ביקש לבדוק משתמשים לא פעילים.")

    try:
        processing_msg = bot.reply_to(message, "מעבד את רשימת המשתמשים, אנא המתן...")

        with data_lock: # קריאה בטוחה של הנתונים מהזיכרון
            # עבודה על עותקים כדי למנוע שינויים בזמן העיבוד ולא להחזיק את המנעול לאורך כל הלולאה
            current_all_seen: Set[int] = all_seen_users.copy()
            current_writing: Set[int] = writing_users.copy()
            details_copy: Dict[int, Dict[str, str]] = user_details.copy()

        # חישוב ההפרש מחוץ למנעול
        inactive_user_ids: Set[int] = current_all_seen - current_writing
        num_inactive = len(inactive_user_ids)
        num_all_seen = len(current_all_seen)
        logger.info(f"נמצאו {num_inactive} משתמשים לא פעילים מתוך {num_all_seen} שנצפו.")

        if not inactive_user_ids:
            bot.edit_message_text("✅ לא נמצאו משתמשים שנצפו אך לא כתבו הודעות.",
                                  chat_id=message.chat.id, message_id=processing_msg.message_id)
            return

        inactive_user_display_list: List[str] = []
        for inactive_id in inactive_user_ids:
            details = details_copy.get(inactive_id)
            display_name = f"👤 ID: {inactive_id}" # ברירת מחדל
            if details:
                first_name = details.get('first_name', '').strip()
                username = details.get('username', '').strip()
                language = details.get('language_code', 'N/A')

                name_parts = []
                if first_name:
                    name_parts.append(first_name)
                if username:
                    name_parts.append(f"(@{username})")
                if not name_parts: # אם אין שם ואין יוזר, השתמש ב-ID
                     name_parts.append(f"ID: {inactive_id}")

                display_name = " ".join(name_parts) + f" | 🌐 {language}"

            inactive_user_display_list.append(display_name)

        if inactive_user_display_list:
            response_header = f"👥 משתמשים לא פעילים ({num_inactive} מתוך {num_all_seen} שנצפו):\n{'-'*25}\n"
            inactive_user_display_list.sort() # מיון הרשימה לנוחות
            response_body = "\n".join(f"- {name}" for name in inactive_user_display_list)
            full_response = response_header + response_body

            # פיצול הודעות ארוכות
            max_len = 4080 # קצת פחות מהמגבלה של טלגרם
            if len(full_response) > max_len:
                 logger.info(f"רשימת הלא פעילים ({num_inactive}) ארוכה, שולח בחלקים למנהל ID={user_id}.")
                 try:
                     bot.edit_message_text(f"רשימת הלא פעילים ארוכה ({num_inactive}), שולח בחלקים...",
                                           message.chat.id, processing_msg.message_id)
                 except Exception as edit_err:
                    logger.warning(f"לא ניתן לערוך הודעת 'מעבד': {edit_err}")
                    bot.send_message(message.chat.id, f"רשימת הלא פעילים ארוכה ({num_inactive}), שולח בחלקים...")


                 lines = response_body.split('\n')
                 current_part = response_header
                 for line in lines:
                     if len(current_part) + len(line) + 1 < max_len:
                         current_part += line + "\n"
                     else:
                         bot.send_message(message.chat.id, current_part.strip())
                         time.sleep(0.6) # המתנה קטנה בין הודעות
                         current_part = line + "\n" # התחל חלק חדש עם השורה הנוכחית
                 if current_part.strip(): # שלח את החלק האחרון אם נשאר משהו
                     bot.send_message(message.chat.id, current_part.strip())
            else:
                # שלח את ההודעה המלאה אם היא קצרה מספיק
                try:
                    bot.edit_message_text(full_response, chat_id=message.chat.id, message_id=processing_msg.message_id)
                except Exception as edit_err:
                     logger.warning(f"לא ניתן לערוך הודעת 'מעבד', שולח כחדשה: {edit_err}")
                     bot.send_message(message.chat.id, full_response)

            logger.info(f"רשימת משתמשים לא פעילים נשלחה למנהל ID={user_id}.")

        else:
             # מצב שלא אמור לקרות אם inactive_user_ids לא ריק, אבל ליתר ביטחון
            bot.edit_message_text("לא הצלחתי ליצור את רשימת המשתמשים הלא פעילים.",
                                  chat_id=message.chat.id, message_id=processing_msg.message_id)
            logger.warning("נוצר מצב לא צפוי: inactive_user_ids לא ריק, אבל inactive_user_display_list ריק.")

    except Exception as e:
        logger.error(f"שגיאה בפקודה /check_inactive: {e}", exc_info=True)
        try:
            # נסה לעדכן את הודעת ה"מעבד" להודעת שגיאה
            bot.edit_message_text(f"אירעה שגיאה בעת בדיקת המשתמשים הלא פעילים. בדוק את הלוגים.",
                                  chat_id=message.chat.id, message_id=processing_msg.message_id)
        except Exception:
             # אם גם העריכה נכשלה, שלח הודעה חדשה
            bot.send_message(message.chat.id, f"אירעה שגיאה חמורה בעת בדיקת המשתמשים הלא פעילים: {e}")


# --- פונקציית הרצה ראשית עם טיפול בשגיאות וניסיונות חוזרים ---
def run_bot():
    """מריץ את הבוט עם לוגיקת ניסיונות חוזרים במקרה של שגיאות."""
    retry_count = 0
    max_retries = 5 # מספר ניסיונות מירבי
    base_wait_time = 5 # זמן המתנה בסיסי בשניות

    while retry_count < max_retries:
        try:
            logger.info(f"הבוט מתחיל polling... (ניסיון {retry_count + 1}/{max_retries})")
            # timeout=30 - זמן מקסימלי להמתנה לתגובה מטלגרם
            # none_stop=True - ממשיך לרוץ גם אם יש שגיאה (אנו מטפלים בה ב-except)
            # interval=1 - המתנה של שנייה בין בקשות polling (יכול להפחית עומס)
            bot.polling(none_stop=True, interval=1, timeout=30)
            # אם הגענו לכאן, polling הפסיק מסיבה לא צפויה (לא שגיאה)
            logger.warning("Polling הופסק באופן לא צפוי.")
            break # צא מהלולאה אם polling הפסיק

        except telebot.apihelper.ApiException as api_ex:
             logger.error(f"שגיאת API של טלגרם: {api_ex}", exc_info=True)
             if "Unauthorized" in str(api_ex):
                 logger.critical("שגיאת אימות (401 Unauthorized). בדוק את תקינות ה-BOT_TOKEN.")
                 break # אין טעם לנסות שוב אם הטוקן שגוי
             # שגיאות אחרות של API, נסה שוב
             retry_count += 1
             wait_time = base_wait_time * (2 ** (retry_count - 1)) # Exponential backoff
             logger.info(f"ממתין {wait_time} שניות לפני ניסיון חוזר...")
             time.sleep(wait_time)

        except requests.exceptions.RequestException as req_ex: # שגיאות רשת
             logger.error(f"שגיאת רשת: {req_ex}", exc_info=True)
             retry_count += 1
             wait_time = base_wait_time * (2 ** (retry_count - 1))
             logger.info(f"ממתין {wait_time} שניות לפני ניסיון חוזר...")
             time.sleep(wait_time)

        except Exception as e:
            retry_count += 1
            logger.error(f"אירעה שגיאה כללית בלתי צפויה: {e}", exc_info=True)
            # המתנה עם זמן המתנה מתארך (exponential backoff)
            wait_time = min(base_wait_time * (2 ** (retry_count - 1)), 300) # המתנה מקסימלית של 5 דקות
            logger.info(f"ממתין {wait_time} שניות לפני ניסיון חוזר...")
            time.sleep(wait_time)

    if retry_count >= max_retries:
        logger.critical("מספר הניסיונות המרבי הושג. הבוט מפסיק לעבוד.")
    else:
        logger.info("הבוט סיים את פעולתו.")


if __name__ == "__main__":
    logger.info("מפעיל את הבוט...")
    run_bot()
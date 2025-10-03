import logging
import json
import os
from io import BytesIO
import re
import datetime
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from dotenv import load_dotenv
from pymongo import MongoClient
from bson.binary import Binary

# Load environment variables
load_dotenv()

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
WAITING_FOR_PHOTO, WAITING_FOR_TEXT = range(2)

# Secure token retrieval
BOT_TOKEN = os.getenv("BOT_TOKEN", "8226242752:AAFRhCf-3zcrhKpTs0vSOyCTB77pKIw8NYc")
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/")
DB_NAME = os.getenv("DB_NAME", "avto_bot_db")  # Using existing database

class MongoStorage:
    """MongoDB storage for license plates and photos"""
    def __init__(self, uri=MONGO_URI, db_name=DB_NAME):
        self.client = MongoClient(uri)
        self.db = self.client[db_name]
        self.records = self.db.license_plates  # New collection for license plates
        print("✅ MongoDB storage ready")

    def save_record(self, text_data, file_data):
        """Save record to MongoDB"""
        normalized_text = self.normalize_text(text_data)
        
        # Create record document
        record = {
            "license_plate": normalized_text,
            "photo_data": Binary(file_data),
            "created_at": datetime.datetime.now()
        }
        
        # Insert into database
        result = self.records.insert_one(record)
        print(f"✅ Saved license plate: '{text_data}' with ID: {result.inserted_id}")
        return True

    def normalize_text(self, text):
        return ' '.join(text.strip().upper().split())

    def search_record(self, search_text):
        """Search records by license plate"""
        normalized_search = self.normalize_text(search_text)
        
        # Find all records with this license plate
        results = list(self.records.find({"license_plate": normalized_search}))
        
        # Extract photo data from results
        photo_data_list = [result['photo_data'] for result in results]
        
        return photo_data_list

    def get_all_plates(self):
        """Get all unique license plates"""
        return self.records.distinct("license_plate")

    def get_stats(self):
        """Get database statistics"""
        total_records = self.records.count_documents({})
        unique_plates = len(self.get_all_plates())
        return {
            "total_records": total_records,
            "unique_plates": unique_plates
        }


class TelegramBot:
    def __init__(self):
        self.storage = MongoStorage()
        self.application = Application.builder().token(BOT_TOKEN).build()

        # Command handlers
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("search", self.search_direct))
        self.application.add_handler(CommandHandler("stats", self.stats))
        self.application.add_handler(CommandHandler("list", self.list_plates))
        self.application.add_handler(CommandHandler("help", self.help_command))
        self.application.add_handler(CommandHandler("check_db", self.check_db_connection))
        self.application.add_handler(CommandHandler("cancel", self.cancel))
        
        # Message handlers - photos and text
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo_auto))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_auto))

    def validate_format(self, text):
        text = ' '.join(text.strip().upper().split())
        # Only the formats you specified
        patterns = [
            r'^\d{2} \d{2} \d{3}$',         # 00 00 000
            r'^\d{3} [A-Z]{2} \d{2}$',      # 000 AB 00
            r'^\d{3} \d{2} \d{2}$',         # 000 00 00
            r'^\d{2} [A-Z]{2} \d{3}$',      # 00 AB 000
            # Without spaces versions
            r'^\d{2}\d{2}\d{3}$',           # 0000000
            r'^\d{3}[A-Z]{2}\d{2}$',        # 000AB00
            r'^\d{3}\d{2}\d{2}$',           # 0000000
            r'^\d{2}[A-Z]{2}\d{3}$',        # 00AB000
        ]
        return any(re.match(pattern, text) for pattern in patterns)

    # === Check DB Connection ===
    async def check_db_connection(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check MongoDB connection"""
        try:
            # Try to get database stats
            db = self.storage.client[self.storage.db.name]
            stats = db.command("dbstats")
            collections = db.list_collection_names()
            
            message = (
                "✅ *MongoDB-ի կապը հաստատված է*\n\n"
                f"*Տվյալների բազա:* {self.storage.db.name}\n"
                f"*Բազայի չափ:* {stats['dataSize'] / (1024*1024):.2f} MB\n"
                f"*Հավաքածուներ:* {', '.join(collections) if collections else 'հավաքածուներ չկան'}\n\n"
                f"*Համարանիշների գրառումներ:* {self.storage.records.count_documents({})}"
            )
            
            await update.message.reply_text(message, parse_mode='Markdown')
        except Exception as e:
            await update.message.reply_text(f"❌ MongoDB-ի միացման սխալ: {str(e)}")

    # === Help Command ===
    async def help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send a message when the command /help is issued."""
        help_text = """
🤖 *Համարանիշների պահպանման և որոնման բոտ*

*Հասանելի հրամաններ:*
/start - ցույց տալ հրահանգներ
/search - որոնել համարանիշով
/list - ցույց տալ բոլոր պահպանված համարները
/stats - տվյալների բազայի վիճակագրություն
/check_db - ստուգել կապը տվյալների բազայի հետ
/help - ցույց տալ այս օգնությունը
/cancel - չեղարկել ընթացիկ գործողությունը

*Ինչպես օգտագործել:*
1. Ուղարկեք նկար ավտոմեքենայի համարանիշով
2. Այնուհետև ուղարկեք համարանիշը տեքստով

*Համարանիշների ֆորմատներ:*
• 00 00 000 կամ 0000000
• 000 AB 00 կամ 000AB00
• 000 00 00 կամ 0000000
• 00 AB 000 կամ 00AB000

*Որոնման օրինակ:*
/search 000AB00
կամ ուղղակի ուղարկեք համարանիշը՝ 000AB00
        """
        await update.message.reply_text(help_text, parse_mode='Markdown')

    # === Start Command ===
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "🤖 Բարի գալուստ համարանիշների բոտ:\n\n"
            "📸 Ուղարկեք լուսանկար ավտոմեքենայի համարանիշով\n"
            "📋 Այնուհետև ուղարկեք համարանիշը տեքստով\n\n"
            "📋 Աջակցվող ֆորմատներ:\n"
            "• 00 00 000 կամ 0000000\n"
            "• 000 AB 00 կամ 000AB00\n"  
            "• 000 00 00 կամ 0000000\n"
            "• 00 AB 000 կամ 00AB000\n\n"
            "Որոնելու համար ուղարկեք /search հրամանը կամ ուղղակի համարանիշը"
        )

    # === Stats Command ===
    async def stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show database statistics"""
        try:
            stats = self.storage.get_stats()
            await update.message.reply_text(
                f"📊 *Տվյալների բազայի վիճակագրություն:*\n"
                f"• Ընդհանուր գրառումներ: {stats['total_records']}\n"
                f"• Եզակի համարներ: {stats['unique_plates']}",
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text("❌ Տվյալների բազայից վիճակագրություն ստանալու սխալ:")

    # === List Command ===
    async def list_plates(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List all saved license plates"""
        try:
            plates = self.storage.get_all_plates()
            
            if not plates:
                await update.message.reply_text("❌ Տվյալների բազայում պահպանված համարներ չկան:")
                return
            
            plates_list = "\n".join([f"• {plate}" for plate in sorted(plates)])
            
            # Split message if too long
            if len(plates_list) > 4000:
                chunks = [plates_list[i:i+4000] for i in range(0, len(plates_list), 4000)]
                for chunk in chunks:
                    await update.message.reply_text(f"📋 Պահպանված համարներ:\n{chunk}")
            else:
                await update.message.reply_text(f"📋 Պահպանված համարներ:\n{plates_list}")
        except Exception as e:
            await update.message.reply_text("❌ Տվյալների բազայից համարների ցուցակ ստանալու սխալ:")

    # === Handle photo automatically ===
    async def handle_photo_auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Automatically handle photos without /start command"""
        try:
            photo = update.message.photo[-1]
            file = await photo.get_file()
            photo_data = await file.download_as_bytearray()
            
            # Store multiple photos if sent together
            if 'photo_data' not in context.user_data:
                context.user_data['photo_data'] = []
            
            context.user_data['photo_data'].append(photo_data)
            
            # Check if this is the first photo in the group
            if len(context.user_data['photo_data']) == 1:
                await update.message.reply_text("📸 Նկարը/ները ստացված է։ Հիմա ուղարկեք համարանիշը:")
            else:
                await update.message.reply_text(f"📸 Ստացվել է {len(context.user_data['photo_data'])} նկար։ Հիմա ուղարկեք համարանիշը:")
                
        except Exception as e:
            logger.error(f"Error processing photo: {e}")
            await update.message.reply_text("❌ Լուսանկարի մշակման սխալ:")

    # === Handle text automatically ===
    async def handle_text_auto(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages automatically"""
        text_data = update.message.text.strip()
        
        # Check if we have photos in context waiting for license plate
        if 'photo_data' in context.user_data and context.user_data['photo_data']:
            # This is a license plate for the previously sent photos
            if not self.validate_format(text_data):
                await update.message.reply_text(
                    "❌ Սխալ ֆորմատ: Օգտագործեք:\n"
                    "• 00 00 000 կամ 0000000\n"
                    "• 000 AB 00 կամ 000AB00\n"
                    "• 000 00 00 կամ 0000000\n"
                    "• 00 AB 000 կամ 00AB000"
                )
                return

            try:
                photo_data_list = context.user_data['photo_data']
                for photo_data in photo_data_list:
                    self.storage.save_record(text_data, photo_data)
                
                await update.message.reply_text(f"✅ {len(photo_data_list)} նկար հաջողությամբ պահպանվել են տվյալների բազայում:")
                context.user_data.clear()
            except Exception as e:
                logger.error(f"Error saving: {e}")
                await update.message.reply_text("❌ Տվյալների պահպանման սխալ:")
        
        else:
            # If no photo in context, treat as search request
            await self.perform_search(update, text_data)

    # === Search Command ===
    async def search_direct(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Search using /search command"""
        if not context.args:
            await update.message.reply_text(
                "🔍 Ուղարկեք համարանիշ /search-ից հետո:\n\n"
                "Օրինակներ:\n"
                "/search 000AB00\n"
                "/search 00AB000\n"
                "/search 0000000\n\n"
                "Կամ ուղղակի ուղարկեք համարանիշը՝ առանց /search հրամանի"
            )
            return

        search_text = ' '.join(context.args).strip()
        await self.perform_search(update, search_text)

    # === Perform search ===
    async def perform_search(self, update: Update, search_text):
        """Perform the actual search operation"""
        if not self.validate_format(search_text):
            await update.message.reply_text(
                "❌ Սխալ ֆորմատ: Օգտագործեք:\n"
                "• 00 00 000 կամ 0000000\n"
                "• 000 AB 00 կամ 000AB00\n"
                "• 000 00 00 կամ 0000000\n"
                "• 00 AB 000 կամ 00AB000"
            )
            return

        try:
            results = self.storage.search_record(search_text)
            if not results:
                await update.message.reply_text("❌ Տվյալներ չեն գտնվել:")
                return

            # Send all photos for this license plate
            for i, photo_bytes in enumerate(results):
                photo_stream = BytesIO(photo_bytes)
                photo_stream.name = f'photo_{i+1}.jpg'
                await update.message.reply_photo(
                    photo=photo_stream,
                    caption=f"🔢 Համարանիշ: {search_text}"
                )

        except Exception as e:
            logger.error(f"Search error: {e}")
            await update.message.reply_text("❌ Որոնման սխալ:")

    # === Cancel ===
    async def cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        context.user_data.clear()
        await update.message.reply_text("Գործողությունը չեղարկված է:")

    def run(self):
        print("🤖 Բոտը գործարկված է MongoDB-ով...")
        self.application.run_polling()

if __name__ == '__main__':
    bot = TelegramBot()
    bot.run()
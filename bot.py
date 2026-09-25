import os
import logging
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatType
from telegram.error import BadRequest

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)


# =========================================================
# CONFIG
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
VOTING_CHANNEL_ID = os.getenv("VOTING_CHANNEL_ID")

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "photo_rating")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "change-this-secret")

BOT_USERNAME = "mytxtgenbot"

MINIMUM_LEADERBOARD_VOTES = 10

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing")

if not VOTING_CHANNEL_ID:
    raise RuntimeError("VOTING_CHANNEL_ID is missing")

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is missing")


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# MONGODB
# =========================================================

mongo_client = MongoClient(MONGODB_URI)

db = mongo_client[MONGODB_DATABASE]

submissions_collection = db["submissions"]
votes_collection = db["votes"]


# Prevent duplicate votes
votes_collection.create_index(
    [
        ("submission_id", ASCENDING),
        ("user_id", ASCENDING),
    ],
    unique=True,
)

# Useful indexes
submissions_collection.create_index(
    [("user_id", ASCENDING)]
)

submissions_collection.create_index(
    [("channel_message_id", ASCENDING)]
)

votes_collection.create_index(
    [("submission_id", ASCENDING)]
)


# =========================================================
# USER SESSION STATE
# =========================================================

waiting_for_photo = set()

waiting_for_caption = {}


# =========================================================
# RULES
# =========================================================

RULES_TEXT = """
📸 PHOTO RATING COMMUNITY

How it works:

1️⃣ Send your photo.
2️⃣ Add a caption.
3️⃣ Your photo will be posted in the voting channel.
4️⃣ Members can rate it from ⭐ 1 to ⭐ 5.
5️⃣ One vote per person for each submission.

🚫 RULES

• No self-voting.
• No vote manipulation.
• No multiple-account voting.
• No spam or repeated submissions.
• Respect other members.
• Do not submit illegal or harmful content.
• Admins may remove content that violates the rules.

🏆 LEADERBOARD

• Minimum 10 votes required.
• Members are ranked by average rating.
• Vote count is used as a secondary ranking factor.

Have fun and post your best shot! 📸
"""


# =========================================================
# SAFE CALLBACK ANSWER
# =========================================================

async def safe_answer(query, text=None, show_alert=False):
    try:
        await query.answer(
            text=text,
            show_alert=show_alert
        )
    except BadRequest as e:
        if "query is too old" in str(e).lower():
            logger.warning("Expired callback query.")
        elif "query id is invalid" in str(e).lower():
            logger.warning("Invalid callback query.")
        else:
            logger.warning(f"Callback answer error: {e}")


# =========================================================
# KEYBOARD
# =========================================================

def rating_keyboard(submission_id):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⭐ 1",
                callback_data=f"rate:{submission_id}:1"
            ),
            InlineKeyboardButton(
                "⭐ 2",
                callback_data=f"rate:{submission_id}:2"
            ),
            InlineKeyboardButton(
                "⭐ 3",
                callback_data=f"rate:{submission_id}:3"
            ),
        ],
        [
            InlineKeyboardButton(
                "⭐ 4",
                callback_data=f"rate:{submission_id}:4"
            ),
            InlineKeyboardButton(
                "⭐ 5",
                callback_data=f"rate:{submission_id}:5"
            ),
        ],
        [
            InlineKeyboardButton(
                "📸 Post Yours",
                url=f"https://t.me/{BOT_USERNAME}"
            ),
            InlineKeyboardButton(
                "🏆 Leaderboard",
                callback_data="leaderboard_popup"
            ),
        ],
    ])


# =========================================================
# START
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != ChatType.PRIVATE:
        return

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📸 Submit Photo",
                callback_data="submit_photo"
            )
        ],
        [
            InlineKeyboardButton(
                "📜 Rules",
                callback_data="show_rules"
            ),
        ],
    ])

    await update.message.reply_text(
        """
👋 Welcome to the Photo Rating Community!

📸 Submit your photo
⭐ Let the community rate it
🏆 Compete for the leaderboard

Tap below to submit your photo.
""",
        reply_markup=keyboard
    )


# =========================================================
# SUBMIT COMMAND
# =========================================================

async def submit_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != ChatType.PRIVATE:
        return

    waiting_for_photo.add(update.effective_user.id)

    await update.message.reply_text(
        "📸 Send me your photo now.\n\n"
        "After that, I'll ask you for a caption."
    )


# =========================================================
# PHOTO HANDLER
# =========================================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != ChatType.PRIVATE:
        return

    user_id = update.effective_user.id

    if user_id not in waiting_for_photo:
        await update.message.reply_text(
            "Please tap 📸 Submit Photo first."
        )
        return

    waiting_for_photo.discard(user_id)

    photo = update.message.photo[-1]

    waiting_for_caption[user_id] = {
        "photo_id": photo.file_id
    }

    await update.message.reply_text(
        "✅ Photo received!\n\n"
        "Now send the caption you want displayed with your photo."
    )


# =========================================================
# CAPTION HANDLER
# =========================================================

async def handle_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != ChatType.PRIVATE:
        return

    user = update.effective_user
    user_id = user.id

    if user_id not in waiting_for_caption:
        return

    caption_text = update.message.text.strip()

    if not caption_text:
        await update.message.reply_text(
            "Please send a valid caption."
        )
        return

    session = waiting_for_caption.pop(user_id)

    username = user.username
    first_name = user.first_name or "User"

    # -----------------------------------------------------
    # Create submission
    # -----------------------------------------------------

    submission = {
        "user_id": user_id,
        "username": username,
        "first_name": first_name,
        "photo_id": session["photo_id"],
        "caption": caption_text,
        "channel_message_id": None,
        "created_at": datetime.now(timezone.utc),
    }

    result = submissions_collection.insert_one(submission)

    submission_id = str(result.inserted_id)

    # -----------------------------------------------------
    # Channel caption
    # -----------------------------------------------------

    display_username = (
        f"@{username}"
        if username
        else first_name
    )

    channel_caption = (
        f"📸 SUBMISSION\n\n"
        f"{caption_text}\n\n"
        f"👤 {display_username}\n\n"
        f"⭐ RATE THIS SUBMISSION\n"
        f"📊 Rating: No votes yet\n"
        f"👥 Votes: 0"
    )

    # -----------------------------------------------------
    # Post to voting channel
    # -----------------------------------------------------

    try:

        sent_message = await context.bot.send_photo(
            chat_id=VOTING_CHANNEL_ID,
            photo=session["photo_id"],
            caption=channel_caption,
            reply_markup=rating_keyboard(submission_id),
        )

    except Exception as e:

        logger.exception(
            f"Failed to post submission {submission_id}: {e}"
        )

        submissions_collection.delete_one(
            {"_id": result.inserted_id}
        )

        await update.message.reply_text(
            "❌ I couldn't post your submission right now.\n"
            "Please try again later."
        )

        return

    # -----------------------------------------------------
    # Save Telegram message ID
    # -----------------------------------------------------

    submissions_collection.update_one(
        {"_id": result.inserted_id},
        {
            "$set": {
                "channel_message_id": sent_message.message_id
            }
        }
    )

    await update.message.reply_text(
        "✅ Your photo has been submitted!\n\n"
        "It is now available in the voting channel for members to rate. ⭐"
    )


# =========================================================
# VOTING
# =========================================================

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    data = query.data

    if not data.startswith("rate:"):
        return

    try:

        _, submission_id, rating_text = data.split(":")

        rating = int(rating_text)

    except Exception:

        await safe_answer(
            query,
            "Invalid vote.",
            True
        )

        return

    if rating < 1 or rating > 5:

        await safe_answer(
            query,
            "Invalid rating.",
            True
        )

        return

    user_id = query.from_user.id

    # -----------------------------------------------------
    # Find submission
    # -----------------------------------------------------

    submission = submissions_collection.find_one(
        {"_id": __import__("bson").ObjectId(submission_id)}
    )

    if not submission:

        await safe_answer(
            query,
            "Submission not found.",
            True
        )

        return

    # -----------------------------------------------------
    # Prevent self-voting
    # -----------------------------------------------------

    if submission["user_id"] == user_id:

        await safe_answer(
            query,
            "🚫 You cannot vote on your own submission.",
            True
        )

        return

    # -----------------------------------------------------
    # Prevent duplicate votes
    # -----------------------------------------------------

    existing_vote = votes_collection.find_one({
        "submission_id": submission_id,
        "user_id": user_id
    })

    if existing_vote:

        await safe_answer(
            query,
            "You have already voted on this submission.",
            True
        )

        return

    # -----------------------------------------------------
    # Save vote
    # -----------------------------------------------------

    try:

        votes_collection.insert_one({
            "submission_id": submission_id,
            "user_id": user_id,
            "rating": rating,
            "created_at": datetime.now(timezone.utc),
        })

    except DuplicateKeyError:

        await safe_answer(
            query,
            "You have already voted on this submission.",
            True
        )

        return

    # -----------------------------------------------------
    # Calculate rating
    # -----------------------------------------------------

    vote_stats = list(
        votes_collection.aggregate([
            {
                "$match": {
                    "submission_id": submission_id
                }
            },
            {
                "$group": {
                    "_id": None,
                    "average": {"$avg": "$rating"},
                    "count": {"$sum": 1}
                }
            }
        ])
    )

    if vote_stats:

        average = vote_stats[0]["average"]
        vote_count = vote_stats[0]["count"]

    else:

        average = 0
        vote_count = 0

    # -----------------------------------------------------
    # Update channel post
    # -----------------------------------------------------

    username = submission.get("username")
    first_name = submission.get("first_name", "User")

    display_username = (
        f"@{username}"
        if username
        else first_name
    )

    new_caption = (
        f"📸 SUBMISSION\n\n"
        f"{submission['caption']}\n\n"
        f"👤 {display_username}\n\n"
        f"⭐ RATE THIS SUBMISSION\n"
        f"📊 Rating: {average:.2f}/5\n"
        f"👥 Votes: {vote_count}"
    )

    try:

        await context.bot.edit_message_caption(
            chat_id=VOTING_CHANNEL_ID,
            message_id=submission["channel_message_id"],
            caption=new_caption,
            reply_markup=rating_keyboard(submission_id),
        )

    except Exception as e:

        logger.warning(
            f"Could not update channel message: {e}"
        )

    await safe_answer(
        query,
        f"⭐ You rated this {rating}/5",
        False
    )


# =========================================================
# LEADERBOARD
# =========================================================

def get_leaderboard():

    pipeline = [
        {
            "$lookup": {
                "from": "votes",
                "let": {
                    "submission_id_str": {
                        "$toString": "$_id"
                    }
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$eq": [
                                    "$submission_id",
                                    "$$submission_id_str"
                                ]
                            }
                        }
                    }
                ],
                "as": "submission_votes"
            }
        },

        {
            "$unwind": "$submission_votes"
        },

        {
            "$group": {
                "_id": "$user_id",
                "username": {
                    "$first": "$username"
                },
                "first_name": {
                    "$first": "$first_name"
                },
                "average_rating": {
                    "$avg": "$submission_votes.rating"
                },
                "vote_count": {
                    "$sum": 1
                }
            }
        },

        {
            "$match": {
                "vote_count": {
                    "$gte": MINIMUM_LEADERBOARD_VOTES
                }
            }
        },

        {
            "$sort": {
                "average_rating": -1,
                "vote_count": -1
            }
        },

        {
            "$limit": 5
        }
    ]

    return list(
        submissions_collection.aggregate(pipeline)
    )


def leaderboard_popup_text():

    leaders = get_leaderboard()

    if not leaders:

        return (
            "🏆 LEADERBOARD\n\n"
            "No members have reached "
            f"{MINIMUM_LEADERBOARD_VOTES} votes yet."
        )

    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

    lines = [
        "🏆 LEADERBOARD",
        ""
    ]

    for index, leader in enumerate(leaders):

        username = leader.get("username")
        first_name = leader.get("first_name") or "User"

        display_name = (
            f"@{username}"
            if username
            else first_name
        )

        average = leader["average_rating"]
        votes = leader["vote_count"]

        lines.append(
            f"{medals[index]} {display_name}\n"
            f"⭐ {average:.2f} • {votes} votes"
        )

        lines.append("")

    return "\n".join(lines)


# =========================================================
# LEADERBOARD BUTTON
# =========================================================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    data = query.data

    if data == "submit_photo":

        if query.message.chat.type != ChatType.PRIVATE:

            await safe_answer(
                query,
                "Please use the bot privately to submit.",
                True
            )

            return

        waiting_for_photo.add(query.from_user.id)

        await safe_answer(query)

        await query.message.reply_text(
            "📸 Send me your photo now."
        )

        return

    if data == "show_rules":

        await safe_answer(query)

        await query.message.reply_text(
            RULES_TEXT
        )

        return

    if data == "leaderboard_popup":

        leaderboard = leaderboard_popup_text()

        # Popup is visible only to the user who clicked.
        await safe_answer(
            query,
            leaderboard,
            True
        )

        return


# =========================================================
# /LEADERBOARD
# =========================================================

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    leaderboard = leaderboard_popup_text()

    await update.message.reply_text(
        leaderboard
    )


# =========================================================
# /MYSTATS
# =========================================================

async def mystats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    submissions = list(
        submissions_collection.find({
            "user_id": user_id
        })
    )

    submission_ids = [
        str(item["_id"])
        for item in submissions
    ]

    if not submission_ids:

        await update.message.reply_text(
            "📊 You haven't submitted anything yet."
        )

        return

    votes_received = votes_collection.count_documents({
        "submission_id": {
            "$in": submission_ids
        }
    })

    vote_stats = list(
        votes_collection.aggregate([
            {
                "$match": {
                    "submission_id": {
                        "$in": submission_ids
                    }
                }
            },
            {
                "$group": {
                    "_id": None,
                    "average": {
                        "$avg": "$rating"
                    }
                }
            }
        ])
    )

    if vote_stats:

        average = vote_stats[0]["average"]

        average_text = f"{average:.2f}/5"

    else:

        average_text = "No votes yet"

    await update.message.reply_text(
        "📊 YOUR STATS\n\n"
        f"📸 Submissions: {len(submissions)}\n"
        f"👥 Votes received: {votes_received}\n"
        f"⭐ Average rating: {average_text}"
    )


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):

    logger.exception(
        "Exception while handling update:",
        exc_info=context.error
    )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info("Starting bot...")

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # Commands
    application.add_handler(
        CommandHandler("start", start)
    )

    application.add_handler(
        CommandHandler("submit", submit_command)
    )

    application.add_handler(
        CommandHandler("leaderboard", leaderboard_command)
    )

    application.add_handler(
        CommandHandler("mystats", mystats_command)
    )

    # Photo
    application.add_handler(
        MessageHandler(
            filters.PHOTO & filters.ChatType.PRIVATE,
            handle_photo
        )
    )

    # Caption
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND
            & filters.ChatType.PRIVATE,
            handle_caption
        )
    )

    # Voting / buttons
    application.add_handler(
        CallbackQueryHandler(
            handle_vote,
            pattern=r"^rate:"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    application.add_error_handler(
        error_handler
    )

    # -----------------------------------------------------
    # Render webhook
    # -----------------------------------------------------

    port = int(os.environ.get("PORT", "10000"))

    render_hostname = os.environ.get(
        "RENDER_EXTERNAL_HOSTNAME"
    )

    if render_hostname:

        webhook_url = (
            f"https://{render_hostname}/telegram"
        )

        logger.info(
            f"Starting webhook: {webhook_url}"
        )

        application.run_webhook(
            listen="0.0.0.0",
            port=port,
            url_path="telegram",
            webhook_url=webhook_url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=False,
        )

    else:

        # Local testing
        logger.info(
            "RENDER_EXTERNAL_HOSTNAME not found."
        )

        logger.info(
            "Running local polling mode."
        )

        application.run_polling(
            drop_pending_updates=False
        )


if __name__ == "__main__":
    main()
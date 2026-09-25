```python
import os
import logging
from datetime import datetime

from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from bson import ObjectId

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.error import BadRequest


# ============================================================
# ENVIRONMENT
# ============================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
VOTING_CHANNEL_ID = int(os.getenv("VOTING_CHANNEL_ID"))

MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DATABASE = os.getenv("MONGODB_DATABASE", "photo_rating")

WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")

PREMIUM_CHANNEL_ID = int(os.getenv("PREMIUM_CHANNEL_ID"))
PREMIUM_CHANNEL_LINK = os.getenv(
    "PREMIUM_CHANNEL_LINK",
    "https://t.me/+F6hT6ThEG6g0MDc1",
)

MINIMUM_LEADERBOARD_VOTES = 10


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# MONGODB
# ============================================================

mongo_client = MongoClient(MONGODB_URI)

db = mongo_client[MONGODB_DATABASE]

submissions_collection = db["submissions"]
votes_collection = db["votes"]


# Prevent duplicate votes for the same submission/user.
votes_collection.create_index(
    [
        ("submission_id", ASCENDING),
        ("user_id", ASCENDING),
    ],
    unique=True,
)

submissions_collection.create_index(
    [("user_id", ASCENDING)]
)

submissions_collection.create_index(
    [("channel_message_id", ASCENDING)]
)

votes_collection.create_index(
    [("submission_id", ASCENDING)]
)


# ============================================================
# TEMPORARY SUBMISSION STATE
# ============================================================

waiting_for_photo = set()
waiting_for_caption = {}

waiting_for_instagram_choice = set()
waiting_for_instagram = set()

waiting_for_whatsapp_choice = set()
waiting_for_whatsapp = set()


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def user_display_name(user):
    if user.username:
        return f"@{user.username}"

    return user.first_name or "User"


def safe_text(value):
    if value is None:
        return ""

    return str(value)


async def safe_answer(query, text=None, show_alert=False):
    try:
        await query.answer(
            text=text,
            show_alert=show_alert,
        )
    except BadRequest:
        pass
    except Exception:
        pass


# ============================================================
# LEADERBOARD
# ============================================================

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


# ============================================================
# RATING KEYBOARD
# ============================================================

def rating_keyboard(submission_id, has_instagram=False, has_whatsapp=False):

    buttons = [
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
    ]

    contact_buttons = []

    if has_instagram:
        contact_buttons.append(
            InlineKeyboardButton(
                "📷 View Instagram",
                callback_data=f"instagram:{submission_id}"
            )
        )

    if has_whatsapp:
        contact_buttons.append(
            InlineKeyboardButton(
                "📱 View WhatsApp",
                callback_data=f"whatsapp:{submission_id}"
            )
        )

    if contact_buttons:
        buttons.append(contact_buttons)

    buttons.append(
        [
            InlineKeyboardButton(
                "📸 Post Yours",
                url="https://t.me/mytxtgenbot"
            ),

            InlineKeyboardButton(
                "🏆 Leaderboard",
                callback_data="leaderboard_popup"
            ),
        ]
    )

    return InlineKeyboardMarkup(buttons)


# ============================================================
# START
# ============================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    waiting_for_photo.add(user.id)

    waiting_for_caption.pop(user.id, None)

    waiting_for_instagram_choice.discard(user.id)
    waiting_for_instagram.discard(user.id)

    waiting_for_whatsapp_choice.discard(user.id)
    waiting_for_whatsapp.discard(user.id)

    await update.message.reply_text(
        "📸 Welcome!\n\n"
        "Send me the photo you want to submit."
    )


# ============================================================
# SUBMIT COMMAND
# ============================================================

async def submit(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    waiting_for_photo.add(user.id)

    waiting_for_caption.pop(user.id, None)

    waiting_for_instagram_choice.discard(user.id)
    waiting_for_instagram.discard(user.id)

    waiting_for_whatsapp_choice.discard(user.id)
    waiting_for_whatsapp.discard(user.id)

    await update.message.reply_text(
        "📸 Send your photo."
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    if user.id not in waiting_for_photo:
        return

    photo = update.message.photo[-1]

    waiting_for_photo.discard(user.id)

    waiting_for_caption[user.id] = {
        "file_id": photo.file_id
    }

    await update.message.reply_text(
        "📝 Now send your caption."
    )


# ============================================================
# CAPTION HANDLER
# ============================================================

async def handle_caption(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    if user.id not in waiting_for_caption:
        return

    caption = update.message.text.strip()

    if not caption:
        await update.message.reply_text(
            "❌ Please send a valid caption."
        )
        return

    data = waiting_for_caption[user.id]

    data["caption"] = caption

    waiting_for_instagram_choice.add(user.id)

    await update.message.reply_text(
        "📷 Do you want to add your Instagram ID?\n\n"
        "This will be available only to Premium members.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Yes",
                        callback_data="instagram_yes"
                    ),
                    InlineKeyboardButton(
                        "❌ No",
                        callback_data="instagram_no"
                    ),
                ]
            ]
        )
    )


# ============================================================
# INSTAGRAM CHOICE
# ============================================================

async def instagram_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await safe_answer(query)

    user = query.from_user

    if user.id not in waiting_for_instagram_choice:
        return

    waiting_for_instagram_choice.discard(user.id)

    if query.data == "instagram_yes":

        waiting_for_instagram.add(user.id)

        await query.edit_message_text(
            "📷 Send your Instagram username.\n\n"
            "Example:\n"
            "@yourusername"
        )

        return

    # NO INSTAGRAM

    waiting_for_whatsapp_choice.add(user.id)

    await query.edit_message_text(
        "📱 Do you want to add your WhatsApp number?\n\n"
        "This will be available only to Premium members.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Yes",
                        callback_data="whatsapp_yes"
                    ),
                    InlineKeyboardButton(
                        "❌ No",
                        callback_data="whatsapp_no"
                    ),
                ]
            ]
        )
    )


# ============================================================
# INSTAGRAM USERNAME
# ============================================================

async def handle_instagram(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    if user.id not in waiting_for_instagram:
        return

    instagram = update.message.text.strip()

    if instagram.startswith("https://instagram.com/"):
        instagram = instagram.replace(
            "https://instagram.com/",
            ""
        )

    if instagram.startswith("https://www.instagram.com/"):
        instagram = instagram.replace(
            "https://www.instagram.com/",
            ""
        )

    instagram = instagram.strip("/ ")

    if instagram.startswith("@"):
        instagram = instagram[1:]

    if not instagram:

        await update.message.reply_text(
            "❌ Please send a valid Instagram username."
        )

        return

    waiting_for_instagram.discard(user.id)

    if user.id not in waiting_for_caption:
        return

    waiting_for_caption[user.id]["instagram_id"] = instagram

    waiting_for_whatsapp_choice.add(user.id)

    await update.message.reply_text(
        "📱 Do you want to add your WhatsApp number?\n\n"
        "This will be available only to Premium members.",
        reply_markup=InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Yes",
                        callback_data="whatsapp_yes"
                    ),
                    InlineKeyboardButton(
                        "❌ No",
                        callback_data="whatsapp_no"
                    ),
                ]
            ]
        )
    )


# ============================================================
# WHATSAPP CHOICE
# ============================================================

async def whatsapp_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await safe_answer(query)

    user = query.from_user

    if user.id not in waiting_for_whatsapp_choice:
        return

    waiting_for_whatsapp_choice.discard(user.id)

    if query.data == "whatsapp_yes":

        waiting_for_whatsapp.add(user.id)

        await query.edit_message_text(
            "📱 Send your WhatsApp number.\n\n"
            "Include the country code.\n"
            "Example: +919876543210"
        )

        return

    # NO WHATSAPP

    await query.edit_message_text(
        "✅ Contact setup complete.\n\n"
        "Posting your submission..."
    )

    await create_submission(
        update,
        context,
        user.id
    )


# ============================================================
# WHATSAPP NUMBER
# ============================================================

async def handle_whatsapp(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    if user.id not in waiting_for_whatsapp:
        return

    whatsapp = update.message.text.strip()

    if not whatsapp:

        await update.message.reply_text(
            "❌ Please send a valid WhatsApp number."
        )

        return

    waiting_for_whatsapp.discard(user.id)

    if user.id not in waiting_for_caption:
        return

    waiting_for_caption[user.id]["whatsapp_number"] = whatsapp

    await update.message.reply_text(
        "✅ Contact setup complete.\n\n"
        "Posting your submission..."
    )

    await create_submission(
        update,
        context,
        user.id
    )


# ============================================================
# CREATE SUBMISSION
# ============================================================

async def create_submission(update, context, user_id):

    if user_id not in waiting_for_caption:
        return

    data = waiting_for_caption.pop(user_id)

    user = update.effective_user

    file_id = data["file_id"]

    caption = data["caption"]

    instagram_id = data.get("instagram_id")

    whatsapp_number = data.get("whatsapp_number")

    share_instagram = bool(instagram_id)

    share_whatsapp = bool(whatsapp_number)

    submission = {
        "user_id": user.id,

        "username": user.username,

        "first_name": user.first_name,

        "file_id": file_id,

        "caption": caption,

        "instagram_id": instagram_id,

        "whatsapp_number": whatsapp_number,

        "share_instagram": share_instagram,

        "share_whatsapp": share_whatsapp,

        "created_at": datetime.utcnow(),

        "channel_message_id": None,

        "premium_message_id": None,
    }

    result = submissions_collection.insert_one(
        submission
    )

    submission_id = str(result.inserted_id)

    # --------------------------------------------------------
    # PUBLIC VOTING CHANNEL
    # --------------------------------------------------------

    public_caption = (
        f"📸 <b>New Submission</b>\n\n"
        f"{caption}\n\n"
        f"⭐ <b>Average:</b> No votes yet\n"
        f"🗳 <b>Votes:</b> 0"
    )

    public_message = await context.bot.send_photo(
        chat_id=VOTING_CHANNEL_ID,

        photo=file_id,

        caption=public_caption,

        parse_mode="HTML",

        reply_markup=rating_keyboard(
            submission_id,

            has_instagram=share_instagram,

            has_whatsapp=share_whatsapp,
        ),
    )

    submissions_collection.update_one(
        {
            "_id": result.inserted_id
        },

        {
            "$set": {
                "channel_message_id": public_message.message_id
            }
        }
    )

    # --------------------------------------------------------
    # PREMIUM CHANNEL
    # --------------------------------------------------------

    if share_instagram or share_whatsapp:

        premium_lines = [
            "💎 <b>PREMIUM SUBMISSION</b>",
            "",
            caption,
            "",
        ]

        if share_instagram:

            premium_lines.extend(
                [
                    "📷 <b>Instagram:</b>",
                    f"@{instagram_id}",
                    "",
                ]
            )

        if share_whatsapp:

            premium_lines.extend(
                [
                    "📱 <b>WhatsApp:</b>",
                    whatsapp_number,
                    "",
                ]
            )

        premium_lines.append(
            "🔒 Premium members only."
        )

        premium_caption = "\n".join(
            premium_lines
        )

        try:

            premium_message = await context.bot.send_photo(
                chat_id=PREMIUM_CHANNEL_ID,

                photo=file_id,

                caption=premium_caption,

                parse_mode="HTML",
            )

            submissions_collection.update_one(
                {
                    "_id": result.inserted_id
                },

                {
                    "$set": {
                        "premium_message_id":
                            premium_message.message_id
                    }
                }
            )

        except Exception as e:

            logger.exception(
                "Failed to post to Premium channel: %s",
                e
            )

    # --------------------------------------------------------
    # USER CONFIRMATION
    # --------------------------------------------------------

    confirmation = (
        "✅ <b>Your submission is live!</b>\n\n"
        "Your photo has been posted to the voting channel."
    )

    if share_instagram or share_whatsapp:

        confirmation += (
            "\n\n💎 Your opted-in contact details "
            "were also posted to the Premium channel."
        )

    await context.bot.send_message(
        chat_id=user.id,

        text=confirmation,

        parse_mode="HTML",
    )


# ============================================================
# CHECK PREMIUM MEMBERSHIP
# ============================================================

async def is_premium_member(bot, user_id):

    try:

        member = await bot.get_chat_member(
            chat_id=PREMIUM_CHANNEL_ID,

            user_id=user_id,
        )

        return member.status in [
            ChatMemberStatus.MEMBER,
            ChatMemberStatus.ADMINISTRATOR,
            ChatMemberStatus.OWNER,
        ]

    except Exception as e:

        logger.exception(
            "Premium membership check failed: %s",
            e
        )

        return False


# ============================================================
# PREMIUM CONTACT POPUP
# ============================================================

async def premium_contact(update, context):

    query = update.callback_query

    await safe_answer(query)

    try:

        _, submission_id = query.data.split(":", 1)

        submission = submissions_collection.find_one(
            {
                "_id": ObjectId(submission_id)
            }
        )

    except Exception:

        await safe_answer(
            query,
            "❌ Submission not found.",
            show_alert=True,
        )

        return

    if not submission:

        await safe_answer(
            query,
            "❌ Submission not found.",
            show_alert=True,
        )

        return

    contact_type = query.data.split(":")[0]

    # --------------------------------------------------------
    # VERIFY PREMIUM MEMBERSHIP
    # --------------------------------------------------------

    premium = await is_premium_member(
        context.bot,
        query.from_user.id,
    )

    if not premium:

        keyboard = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "💎 Join Premium Channel",
                        url=PREMIUM_CHANNEL_LINK,
                    )
                ]
            ]
        )

        await safe_answer(
            query,
            "🔒 Premium members only.\n\n"
            "Join the Premium channel to view "
            "this contact information.",
            show_alert=True,
        )

        # Send private message containing join button.
        try:

            await context.bot.send_message(
                chat_id=query.from_user.id,

                text=(
                    "🔒 <b>PREMIUM CONTENT</b>\n\n"
                    "This contact information is "
                    "available to Premium members.\n\n"
                    "💎 Join the Premium channel "
                    "to view it."
                ),

                parse_mode="HTML",

                reply_markup=keyboard,
            )

        except Exception as e:

            logger.exception(
                "Could not send Premium join message: %s",
                e
            )

        return

    # --------------------------------------------------------
    # PREMIUM MEMBER
    # --------------------------------------------------------

    if contact_type == "instagram":

        instagram = submission.get(
            "instagram_id"
        )

        if not submission.get("share_instagram") or not instagram:

            await safe_answer(
                query,
                "Instagram was not provided by this creator.",
                show_alert=True,
            )

            return

        await safe_answer(
            query,
            f"📷 Instagram\n\n@{instagram}",
            show_alert=True,
        )

        return

    if contact_type == "whatsapp":

        whatsapp = submission.get(
            "whatsapp_number"
        )

        if not submission.get("share_whatsapp") or not whatsapp:

            await safe_answer(
                query,
                "WhatsApp was not provided by this creator.",
                show_alert=True,
            )

            return

        await safe_answer(
            query,
            f"📱 WhatsApp\n\n{whatsapp}",
            show_alert=True,
        )

        return


# ============================================================
# VOTE HANDLER
# ============================================================

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    data = query.data

    try:

        _, submission_id, rating_str = data.split(":")

        rating = int(rating_str)

    except Exception:

        await safe_answer(
            query,
            "❌ Invalid vote.",
            show_alert=True,
        )

        return

    user = query.from_user

    # --------------------------------------------------------
    # GET SUBMISSION
    # --------------------------------------------------------

    try:

        submission = submissions_collection.find_one(
            {
                "_id": ObjectId(submission_id)
            }
        )

    except Exception:

        submission = None

    if not submission:

        await safe_answer(
            query,
            "❌ Submission not found.",
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # SELF VOTE
    # --------------------------------------------------------

    if submission["user_id"] == user.id:

        await safe_answer(
            query,
            "❌ You cannot vote for your own photo.",
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # CHECK EXISTING VOTE
    # --------------------------------------------------------

    existing_vote = votes_collection.find_one(
        {
            "submission_id": submission_id,

            "user_id": user.id,
        }
    )

    if existing_vote:

        await safe_answer(
            query,
            "⚠️ You already voted for this photo.",
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # INSERT VOTE
    # --------------------------------------------------------

    try:

        votes_collection.insert_one(
            {
                "submission_id": submission_id,

                "user_id": user.id,

                "rating": rating,

                "created_at": datetime.utcnow(),
            }
        )

    except Exception:

        await safe_answer(
            query,
            "⚠️ You already voted for this photo.",
            show_alert=True,
        )

        return

    # --------------------------------------------------------
    # CALCULATE STATS
    # --------------------------------------------------------

    votes = list(
        votes_collection.find(
            {
                "submission_id": submission_id
            }
        )
    )

    vote_count = len(votes)

    average_rating = (
        sum(v["rating"] for v in votes)
        / vote_count
        if vote_count
        else 0
    )

    # --------------------------------------------------------
    # UPDATE PUBLIC CHANNEL POST
    # --------------------------------------------------------

    public_caption = (
        f"📸 <b>New Submission</b>\n\n"
        f"{submission['caption']}\n\n"
        f"⭐ <b>Average:</b> "
        f"{average_rating:.2f}/5\n"
        f"🗳 <b>Votes:</b> {vote_count}"
    )

    try:

        await context.bot.edit_message_caption(
            chat_id=VOTING_CHANNEL_ID,

            message_id=submission["channel_message_id"],

            caption=public_caption,

            parse_mode="HTML",

            reply_markup=rating_keyboard(
                submission_id,

                has_instagram=submission.get(
                    "share_instagram",
                    False
                ),

                has_whatsapp=submission.get(
                    "share_whatsapp",
                    False
                ),
            ),
        )

    except Exception as e:

        logger.exception(
            "Could not update voting message: %s",
            e
        )

    await safe_answer(
        query,
        f"⭐ You rated this photo {rating}/5",
        show_alert=True,
    )


# ============================================================
# LEADERBOARD POPUP
# ============================================================

async def leaderboard_popup(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query

    await safe_answer(query)

    leaderboard = get_leaderboard()

    if not leaderboard:

        await safe_answer(
            query,
            "🏆 No members have reached 10 votes yet.",
            show_alert=True,
        )

        return

    lines = [
        "🏆 LEADERBOARD",
        "",
        "Minimum: 10 total votes",
        "",
    ]

    medals = [
        "🥇",
        "🥈",
        "🥉",
        "4️⃣",
        "5️⃣",
    ]

    for index, member in enumerate(
        leaderboard
    ):

        name = member.get(
            "username"
        )

        if name:

            name = f"@{name}"

        else:

            name = member.get(
                "first_name",
                "User"
            )

        average = member.get(
            "average_rating",
            0
        )

        votes = member.get(
            "vote_count",
            0
        )

        medal = medals[index]

        lines.append(
            f"{medal} {name}\n"
            f"   ⭐ {average:.2f}/5 "
            f"• 🗳 {votes} votes"
        )

    text = "\n".join(lines)

    # Telegram callback alerts have a character limit.
    # Keep it safely below that limit.

    if len(text) > 1900:

        text = text[:1900]

    await safe_answer(
        query,
        text,
        show_alert=True,
    )


# ============================================================
# /LEADERBOARD
# ============================================================

async def leaderboard_command(update: Update, context: ContextTypes.DEFAULT_TYPE):

    leaderboard = get_leaderboard()

    if not leaderboard:

        await update.message.reply_text(
            "🏆 No members have reached "
            "10 total votes yet."
        )

        return

    lines = [
        "🏆 <b>LEADERBOARD</b>",
        "",
        "Minimum: 10 total votes",
        "",
    ]

    medals = [
        "🥇",
        "🥈",
        "🥉",
        "4️⃣",
        "5️⃣",
    ]

    for index, member in enumerate(
        leaderboard
    ):

        username = member.get(
            "username"
        )

        if username:

            name = f"@{username}"

        else:

            name = member.get(
                "first_name",
                "User"
            )

        average = member.get(
            "average_rating",
            0
        )

        votes = member.get(
            "vote_count",
            0
        )

        lines.append(
            f"{medals[index]} "
            f"<b>{name}</b>\n"
            f"⭐ {average:.2f}/5 "
            f"• 🗳 {votes} votes"
        )

    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
    )


# ============================================================
# /MYSTATS
# ============================================================

async def mystats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    submissions = list(
        submissions_collection.find(
            {
                "user_id": user_id
            }
        )
    )

    submission_ids = [
        str(s["_id"])
        for s in submissions
    ]

    votes = []

    if submission_ids:

        votes = list(
            votes_collection.find(
                {
                    "submission_id": {
                        "$in": submission_ids
                    }
                }
            )
        )

    total_votes = len(votes)

    average = (
        sum(v["rating"] for v in votes)
        / total_votes
        if total_votes
        else 0
    )

    await update.message.reply_text(
        "📊 <b>Your Stats</b>\n\n"
        f"📸 Submissions: {len(submissions)}\n"
        f"🗳 Total votes received: {total_votes}\n"
        f"⭐ Average rating: {average:.2f}/5",
        parse_mode="HTML",
    )


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(update, context):

    logger.exception(
        "Unhandled exception:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN is missing."
        )

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # --------------------------------------------------------
    # COMMANDS
    # --------------------------------------------------------

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    application.add_handler(
        CommandHandler(
            "submit",
            submit,
        )
    )

    application.add_handler(
        CommandHandler(
            "leaderboard",
            leaderboard_command,
        )
    )

    application.add_handler(
        CommandHandler(
            "mystats",
            mystats,
        )
    )

    # --------------------------------------------------------
    # CALLBACKS
    # --------------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            handle_vote,
            pattern=r"^rate:"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            leaderboard_popup,
            pattern=r"^leaderboard_popup$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            instagram_choice,
            pattern=r"^instagram_(yes|no)$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            whatsapp_choice,
            pattern=r"^whatsapp_(yes|no)$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            premium_contact,
            pattern=r"^(instagram|whatsapp):"
        )
    )

    # --------------------------------------------------------
    # PHOTO
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.PHOTO,
            handle_photo,
        )
    )

    # --------------------------------------------------------
    # TEXT
    # --------------------------------------------------------

    application.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE
            & filters.TEXT
            & ~filters.COMMAND,
            handle_text_router,
        )
    )

    # --------------------------------------------------------
    # ERRORS
    # --------------------------------------------------------

    application.add_error_handler(
        error_handler
    )

    # --------------------------------------------------------
    # RENDER WEBHOOK
    # --------------------------------------------------------

    port = int(
        os.environ.get(
            "PORT",
            "10000"
        )
    )

    render_hostname = os.environ.get(
        "RENDER_EXTERNAL_HOSTNAME"
    )

    if render_hostname:

        webhook_url = (
            f"https://{render_hostname}/telegram"
        )

        logger.info(
            "Starting webhook: %s",
            webhook_url,
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

        logger.info(
            "Starting polling mode."
        )

        application.run_polling(
            drop_pending_updates=False
        )


# ============================================================
# TEXT ROUTER
# ============================================================

async def handle_text_router(update, context):

    user_id = update.effective_user.id

    if user_id in waiting_for_instagram:

        await handle_instagram(
            update,
            context,
        )

        return

    if user_id in waiting_for_whatsapp:

        await handle_whatsapp(
            update,
            context,
        )

        return

    if user_id in waiting_for_caption:

        await handle_caption(
            update,
            context,
        )

        return


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
```

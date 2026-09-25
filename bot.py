import os
import logging
from datetime import datetime
from html import escape

from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING
from bson import ObjectId

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
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

VOTING_CHANNEL_ID = int(
    os.getenv("VOTING_CHANNEL_ID")
)

MONGODB_URI = os.getenv("MONGODB_URI")

MONGODB_DATABASE = os.getenv(
    "MONGODB_DATABASE",
    "photo_rating",
)

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET"
)

# Premium channel
PREMIUM_CHANNEL_ID = int(
    os.getenv(
        "PREMIUM_CHANNEL_ID",
        "-1003936156823",
    )
)

# Telegram paid invite link
PREMIUM_CHANNEL_LINK = os.getenv(
    "PREMIUM_CHANNEL_LINK",
    "https://t.me/+X34Y0FBLteQyNDFl",
)

# Minimum total votes received before appearing
# on leaderboard
MINIMUM_LEADERBOARD_VOTES = 10


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    format=(
        "%(asctime)s - "
        "%(name)s - "
        "%(levelname)s - "
        "%(message)s"
    ),
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ============================================================
# MONGODB
# ============================================================

mongo_client = MongoClient(
    MONGODB_URI
)

db = mongo_client[
    MONGODB_DATABASE
]

submissions_collection = db[
    "submissions"
]

votes_collection = db[
    "votes"
]


# ============================================================
# DATABASE INDEXES
# ============================================================

# Prevent the same user from voting twice
# on the same submission.
votes_collection.create_index(
    [
        ("submission_id", ASCENDING),
        ("user_id", ASCENDING),
    ],
    unique=True,
)

submissions_collection.create_index(
    [
        ("user_id", ASCENDING)
    ]
)

submissions_collection.create_index(
    [
        ("channel_message_id", ASCENDING)
    ]
)

votes_collection.create_index(
    [
        ("submission_id", ASCENDING)
    ]
)


# ============================================================
# TEMPORARY USER SESSION STATE
# ============================================================

waiting_for_photo = set()

waiting_for_caption = {}

waiting_for_instagram_choice = set()

waiting_for_instagram = set()

waiting_for_whatsapp_choice = set()

waiting_for_whatsapp = set()


# ============================================================
# SAFE CALLBACK ANSWER
# ============================================================

async def safe_answer(
    query,
    text=None,
    show_alert=False,
):

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

        # ----------------------------------------------------
        # Match submissions with their votes.
        #
        # submission _id = ObjectId
        # vote submission_id = string
        #
        # Convert ObjectId to string before matching.
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # One document per vote
        # ----------------------------------------------------

        {
            "$unwind": "$submission_votes"
        },

        # ----------------------------------------------------
        # Group all submissions/votes by creator
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Minimum 10 votes
        # ----------------------------------------------------

        {
            "$match": {

                "vote_count": {
                    "$gte": MINIMUM_LEADERBOARD_VOTES
                }

            }

        },

        # ----------------------------------------------------
        # Highest average first
        # ----------------------------------------------------

        {
            "$sort": {

                "average_rating": -1,

                "vote_count": -1

            }

        },

        # ----------------------------------------------------
        # Top 5
        # ----------------------------------------------------

        {
            "$limit": 5
        }

    ]

    return list(
        submissions_collection.aggregate(
            pipeline
        )
    )


# ============================================================
# PUBLIC CHANNEL KEYBOARD
# ============================================================

def rating_keyboard(
    submission_id,
    has_instagram=False,
    has_whatsapp=False,
):

    buttons = [

        # Rating row 1
        [
            InlineKeyboardButton(
                "⭐ 1",
                callback_data=(
                    f"rate:{submission_id}:1"
                ),
            ),

            InlineKeyboardButton(
                "⭐ 2",
                callback_data=(
                    f"rate:{submission_id}:2"
                ),
            ),

            InlineKeyboardButton(
                "⭐ 3",
                callback_data=(
                    f"rate:{submission_id}:3"
                ),
            ),
        ],

        # Rating row 2
        [
            InlineKeyboardButton(
                "⭐ 4",
                callback_data=(
                    f"rate:{submission_id}:4"
                ),
            ),

            InlineKeyboardButton(
                "⭐ 5",
                callback_data=(
                    f"rate:{submission_id}:5"
                ),
            ),
        ],
    ]

    # --------------------------------------------------------
    # Contact buttons
    #
    # These buttons DO NOT contain contact information.
    # They only open the Premium purchase prompt.
    # --------------------------------------------------------

    contact_buttons = []

    if has_instagram:

        contact_buttons.append(
            InlineKeyboardButton(
                "📷 View Instagram",
                callback_data=(
                    f"instagram:{submission_id}"
                ),
            )
        )

    if has_whatsapp:

        contact_buttons.append(
            InlineKeyboardButton(
                "📱 View WhatsApp",
                callback_data=(
                    f"whatsapp:{submission_id}"
                ),
            )
        )

    if contact_buttons:

        buttons.append(
            contact_buttons
        )

    # --------------------------------------------------------
    # Bottom buttons
    # --------------------------------------------------------

    buttons.append(
        [

            InlineKeyboardButton(
                "📸 Post Yours",
                url="https://t.me/mytxtgenbot",
            ),

            InlineKeyboardButton(
                "🏆 Leaderboard",
                callback_data=(
                    "leaderboard_popup"
                ),
            ),

        ]
    )

    return InlineKeyboardMarkup(
        buttons
    )


# ============================================================
# /START
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    # Reset current submission flow
    waiting_for_photo.add(
        user.id
    )

    waiting_for_caption.pop(
        user.id,
        None,
    )

    waiting_for_instagram_choice.discard(
        user.id
    )

    waiting_for_instagram.discard(
        user.id
    )

    waiting_for_whatsapp_choice.discard(
        user.id
    )

    waiting_for_whatsapp.discard(
        user.id
    )

    # Reset Premium prompt tracking
    context.user_data.pop(
        "premium_prompt_message_id",
        None,
    )

    await update.message.reply_text(
        "📸 <b>Welcome!</b>\n\n"
        "Send me the photo you want to submit.",
        parse_mode="HTML",
    )


# ============================================================
# /SUBMIT
# ============================================================

async def submit(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    waiting_for_photo.add(
        user.id
    )

    waiting_for_caption.pop(
        user.id,
        None,
    )

    waiting_for_instagram_choice.discard(
        user.id
    )

    waiting_for_instagram.discard(
        user.id
    )

    waiting_for_whatsapp_choice.discard(
        user.id
    )

    waiting_for_whatsapp.discard(
        user.id
    )

    await update.message.reply_text(
        "📸 Send your photo."
    )


# ============================================================
# PHOTO HANDLER
# ============================================================

async def handle_photo(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    if user.id not in waiting_for_photo:
        return

    photo = update.message.photo[-1]

    waiting_for_photo.discard(
        user.id
    )

    waiting_for_caption[
        user.id
    ] = {

        "file_id": photo.file_id

    }

    await update.message.reply_text(
        "📝 Now send your caption."
    )


# ============================================================
# CAPTION HANDLER
# ============================================================

async def handle_caption(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    if user.id not in waiting_for_caption:
        return

    caption = (
        update.message.text.strip()
    )

    if not caption:

        await update.message.reply_text(
            "❌ Please send a valid caption."
        )

        return

    waiting_for_caption[
        user.id
    ]["caption"] = caption

    waiting_for_instagram_choice.add(
        user.id
    )

    await update.message.reply_text(

        "📷 <b>Do you want to add your "
        "Instagram ID?</b>\n\n"
        "It will be shown only in the "
        "Premium channel.",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            [

                [

                    InlineKeyboardButton(
                        "✅ Yes",
                        callback_data=(
                            "instagram_yes"
                        ),
                    ),

                    InlineKeyboardButton(
                        "❌ No",
                        callback_data=(
                            "instagram_no"
                        ),
                    ),

                ]

            ]
        ),
    )


# ============================================================
# INSTAGRAM YES / NO
# ============================================================

async def instagram_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await safe_answer(
        query
    )

    user = query.from_user

    if user.id not in waiting_for_instagram_choice:
        return

    waiting_for_instagram_choice.discard(
        user.id
    )

    # --------------------------------------------------------
    # YES
    # --------------------------------------------------------

    if query.data == "instagram_yes":

        waiting_for_instagram.add(
            user.id
        )

        await query.edit_message_text(
            "📷 Send your Instagram username.\n\n"
            "Example:\n"
            "@yourusername"
        )

        return

    # --------------------------------------------------------
    # NO
    # --------------------------------------------------------

    waiting_for_whatsapp_choice.add(
        user.id
    )

    await query.edit_message_text(

        "📱 <b>Do you want to add your "
        "WhatsApp number?</b>\n\n"
        "It will be shown only in the "
        "Premium channel.",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            [

                [

                    InlineKeyboardButton(
                        "✅ Yes",
                        callback_data=(
                            "whatsapp_yes"
                        ),
                    ),

                    InlineKeyboardButton(
                        "❌ No",
                        callback_data=(
                            "whatsapp_no"
                        ),
                    ),

                ]

            ]
        ),
    )


# ============================================================
# INSTAGRAM USERNAME
# ============================================================

async def handle_instagram(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    if user.id not in waiting_for_instagram:
        return

    instagram = (
        update.message.text.strip()
    )

    # --------------------------------------------------------
    # Clean Instagram URL
    # --------------------------------------------------------

    instagram = instagram.replace(
        "https://instagram.com/",
        "",
    )

    instagram = instagram.replace(
        "https://www.instagram.com/",
        "",
    )

    instagram = instagram.strip(
        "/ "
    )

    if instagram.startswith("@"):

        instagram = instagram[1:]

    if not instagram:

        await update.message.reply_text(
            "❌ Please send a valid Instagram username."
        )

        return

    waiting_for_instagram.discard(
        user.id
    )

    if user.id not in waiting_for_caption:
        return

    waiting_for_caption[
        user.id
    ]["instagram_id"] = instagram

    waiting_for_whatsapp_choice.add(
        user.id
    )

    await update.message.reply_text(

        "📱 <b>Do you want to add your "
        "WhatsApp number?</b>\n\n"
        "It will be shown only in the "
        "Premium channel.",

        parse_mode="HTML",

        reply_markup=InlineKeyboardMarkup(
            [

                [

                    InlineKeyboardButton(
                        "✅ Yes",
                        callback_data=(
                            "whatsapp_yes"
                        ),
                    ),

                    InlineKeyboardButton(
                        "❌ No",
                        callback_data=(
                            "whatsapp_no"
                        ),
                    ),

                ]

            ]
        ),
    )


# ============================================================
# WHATSAPP YES / NO
# ============================================================

async def whatsapp_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await safe_answer(
        query
    )

    user = query.from_user

    if user.id not in waiting_for_whatsapp_choice:
        return

    waiting_for_whatsapp_choice.discard(
        user.id
    )

    # --------------------------------------------------------
    # YES
    # --------------------------------------------------------

    if query.data == "whatsapp_yes":

        waiting_for_whatsapp.add(
            user.id
        )

        await query.edit_message_text(
            "📱 Send your WhatsApp number.\n\n"
            "Example:\n"
            "+919876543210"
        )

        return

    # --------------------------------------------------------
    # NO
    # --------------------------------------------------------

    await query.edit_message_text(
        "✅ Contact setup complete.\n\n"
        "Posting your submission..."
    )

    await create_submission(
        update,
        context,
        user.id,
    )


# ============================================================
# WHATSAPP NUMBER
# ============================================================

async def handle_whatsapp(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    if update.effective_chat.type != "private":
        return

    user = update.effective_user

    if user.id not in waiting_for_whatsapp:
        return

    whatsapp = (
        update.message.text.strip()
    )

    if not whatsapp:

        await update.message.reply_text(
            "❌ Please send a valid WhatsApp number."
        )

        return

    waiting_for_whatsapp.discard(
        user.id
    )

    if user.id not in waiting_for_caption:
        return

    waiting_for_caption[
        user.id
    ]["whatsapp_number"] = whatsapp

    await update.message.reply_text(
        "✅ Contact setup complete.\n\n"
        "Posting your submission..."
    )

    await create_submission(
        update,
        context,
        user.id,
    )


# ============================================================
# CREATE SUBMISSION
# ============================================================

async def create_submission(
    update,
    context,
    user_id,
):

    if user_id not in waiting_for_caption:
        return

    data = waiting_for_caption.pop(
        user_id
    )

    user = update.effective_user

    file_id = data[
        "file_id"
    ]

    caption = data[
        "caption"
    ]

    instagram_id = data.get(
        "instagram_id"
    )

    whatsapp_number = data.get(
        "whatsapp_number"
    )

    share_instagram = bool(
        instagram_id
    )

    share_whatsapp = bool(
        whatsapp_number
    )

    # ========================================================
    # SAVE SUBMISSION
    # ========================================================

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

    submission_id = str(
        result.inserted_id
    )

    # ========================================================
    # PUBLIC VOTING CHANNEL
    # ========================================================

    public_caption = (

        "📸 <b>New Submission</b>\n\n"

        f"{escape(caption)}\n\n"

        "⭐ <b>Average:</b> No votes yet\n"

        "🗳 <b>Votes:</b> 0"

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

                "channel_message_id":
                    public_message.message_id

            }
        },

    )

    # ========================================================
    # PREMIUM CHANNEL
    #
    # IMPORTANT:
    # NO BUTTONS HERE.
    # ========================================================

    if share_instagram or share_whatsapp:

        premium_lines = [

            "💎 <b>PREMIUM SUBMISSION</b>",

            "",

            escape(caption),

            "",

        ]

        # ----------------------------------------------------
        # Instagram
        # ----------------------------------------------------

        if share_instagram:

            premium_lines.extend(

                [

                    "📷 <b>Instagram:</b> "
                    f"@{escape(instagram_id)}",

                    "",

                ]

            )

        # ----------------------------------------------------
        # WhatsApp
        # ----------------------------------------------------

        if share_whatsapp:

            premium_lines.extend(

                [

                    "📱 <b>WhatsApp:</b> "
                    f"{escape(whatsapp_number)}",

                    "",

                ]

            )

        premium_caption = "\n".join(
            premium_lines
        )

        try:

            premium_message = (
                await context.bot.send_photo(

                    chat_id=PREMIUM_CHANNEL_ID,

                    photo=file_id,

                    caption=premium_caption,

                    parse_mode="HTML",

                    # NO reply_markup
                )
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
                },

            )

        except Exception as e:

            logger.exception(
                "Failed to post Premium submission: %s",
                e,
            )

    # ========================================================
    # USER CONFIRMATION
    # ========================================================

    confirmation = (

        "✅ <b>Your submission is live!</b>\n\n"

        "Your photo has been posted to "
        "the voting channel."

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
# PREMIUM CONTACT BUTTON
# ============================================================
#
# IMPORTANT:
#
# There is NO membership check.
#
# The bot NEVER exposes Instagram/WhatsApp from this button.
#
# It simply directs the user to the paid Telegram Premium
# invite link.
#
# Telegram itself manages payment/subscription.
# ============================================================

async def premium_contact(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    await safe_answer(
        query,
        "🔒 Premium content",
        show_alert=False,
    )

    keyboard = InlineKeyboardMarkup(

        [

            [

                InlineKeyboardButton(

                    "💎 Join Premium — 100 ⭐",

                    url=PREMIUM_CHANNEL_LINK,

                )

            ]

        ]

    )

    premium_text = (

        "🔒 <b>PREMIUM CONTENT</b>\n\n"

        "This creator's contact information "
        "is available in the Premium channel.\n\n"

        "💎 <b>Premium Access</b>\n"
        "100 ⭐ / 30 days\n\n"

        "Join Premium to view the creator's "
        "full contact details."

    )

    # --------------------------------------------------------
    # Prevent duplicate Premium messages during this session
    # --------------------------------------------------------

    existing_message_id = context.user_data.get(
        "premium_prompt_message_id"
    )

    if existing_message_id:

        try:

            await context.bot.edit_message_text(

                chat_id=query.from_user.id,

                message_id=existing_message_id,

                text=premium_text,

                parse_mode="HTML",

                reply_markup=keyboard,

            )

            return

        except Exception:

            context.user_data.pop(
                "premium_prompt_message_id",
                None,
            )

    # --------------------------------------------------------
    # Send Premium purchase message
    # --------------------------------------------------------

    try:

        message = await context.bot.send_message(

            chat_id=query.from_user.id,

            text=premium_text,

            parse_mode="HTML",

            reply_markup=keyboard,

        )

        context.user_data[
            "premium_prompt_message_id"
        ] = message.message_id

    except Exception as e:

        logger.exception(
            "Could not send Premium purchase message: %s",
            e,
        )

        await safe_answer(

            query,

            "Open @mytxtgenbot and press /start first.",

            show_alert=True,

        )


# ============================================================
# VOTE HANDLER
# ============================================================

async def handle_vote(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    data = query.data

    # --------------------------------------------------------
    # Parse callback
    # --------------------------------------------------------

    try:

        _,
        submission_id,
        rating_str = data.split(":")

        rating = int(
            rating_str
        )

    except Exception:

        await safe_answer(

            query,

            "❌ Invalid vote.",

            show_alert=True,

        )

        return

    user = query.from_user

    # --------------------------------------------------------
    # Get submission
    # --------------------------------------------------------

    try:

        submission = (
            submissions_collection.find_one(

                {
                    "_id": ObjectId(
                        submission_id
                    )
                }

            )
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
    # Self vote
    # --------------------------------------------------------

    if submission[
        "user_id"
    ] == user.id:

        await safe_answer(

            query,

            "❌ You cannot vote for your own photo.",

            show_alert=True,

        )

        return

    # --------------------------------------------------------
    # Existing vote
    # --------------------------------------------------------

    existing_vote = (
        votes_collection.find_one(

            {
                "submission_id":
                    submission_id,

                "user_id":
                    user.id,

            }

        )
    )

    if existing_vote:

        await safe_answer(

            query,

            "⚠️ You already voted for this photo.",

            show_alert=True,

        )

        return

    # --------------------------------------------------------
    # Save vote
    # --------------------------------------------------------

    try:

        votes_collection.insert_one(

            {

                "submission_id":
                    submission_id,

                "user_id":
                    user.id,

                "rating":
                    rating,

                "created_at":
                    datetime.utcnow(),

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
    # Calculate stats
    # --------------------------------------------------------

    votes = list(

        votes_collection.find(

            {
                "submission_id":
                    submission_id
            }

        )

    )

    vote_count = len(
        votes
    )

    average_rating = (

        sum(
            v["rating"]
            for v in votes
        )
        / vote_count

        if vote_count

        else 0

    )

    # --------------------------------------------------------
    # Update public channel
    # --------------------------------------------------------

    public_caption = (

        "📸 <b>New Submission</b>\n\n"

        f"{escape(submission['caption'])}\n\n"

        f"⭐ <b>Average:</b> "
        f"{average_rating:.2f}/5\n"

        f"🗳 <b>Votes:</b> "
        f"{vote_count}"

    )

    try:

        await context.bot.edit_message_caption(

            chat_id=VOTING_CHANNEL_ID,

            message_id=
                submission[
                    "channel_message_id"
                ],

            caption=public_caption,

            parse_mode="HTML",

            reply_markup=rating_keyboard(

                submission_id,

                has_instagram=
                    submission.get(
                        "share_instagram",
                        False,
                    ),

                has_whatsapp=
                    submission.get(
                        "share_whatsapp",
                        False,
                    ),

            ),

        )

    except Exception as e:

        logger.exception(

            "Could not update voting message: %s",

            e,

        )

    # --------------------------------------------------------
    # Vote confirmation
    # --------------------------------------------------------

    await safe_answer(

        query,

        f"⭐ You rated this photo {rating}/5",

        show_alert=True,

    )


# ============================================================
# LEADERBOARD POPUP
# ============================================================

async def leaderboard_popup(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    query = update.callback_query

    leaderboard = get_leaderboard()

    if not leaderboard:

        await safe_answer(

            query,

            "🏆 No members have reached 10 votes yet.",

            show_alert=True,

        )

        return

    medals = [

        "🥇",
        "🥈",
        "🥉",
        "4️⃣",
        "5️⃣",

    ]

    lines = [

        "🏆 LEADERBOARD",
        "",
        "Minimum: 10 total votes",
        "",

    ]

    for index, member in enumerate(
        leaderboard
    ):

        username = member.get(
            "username"
        )

        if username:

            name = (
                f"@{username}"
            )

        else:

            name = member.get(
                "first_name",
                "User",
            )

        average = member.get(
            "average_rating",
            0,
        )

        vote_count = member.get(
            "vote_count",
            0,
        )

        lines.append(

            f"{medals[index]} "
            f"{name}\n"
            f"   ⭐ {average:.2f}/5 "
            f"• 🗳 {vote_count} votes"

        )

    text = "\n".join(
        lines
    )

    # Telegram callback alert size safety
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

async def leaderboard_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    leaderboard = get_leaderboard()

    if not leaderboard:

        await update.message.reply_text(

            "🏆 No members have reached "
            "10 total votes yet."

        )

        return

    medals = [

        "🥇",
        "🥈",
        "🥉",
        "4️⃣",
        "5️⃣",

    ]

    lines = [

        "🏆 <b>LEADERBOARD</b>",
        "",
        "Minimum: 10 total votes",
        "",

    ]

    for index, member in enumerate(
        leaderboard
    ):

        username = member.get(
            "username"
        )

        if username:

            name = (
                f"@{username}"
            )

        else:

            name = member.get(
                "first_name",
                "User",
            )

        average = member.get(
            "average_rating",
            0,
        )

        vote_count = member.get(
            "vote_count",
            0,
        )

        lines.append(

            f"{medals[index]} "
            f"<b>{escape(name)}</b>\n"
            f"⭐ {average:.2f}/5 "
            f"• 🗳 {vote_count} votes"

        )

    await update.message.reply_text(

        "\n".join(lines),

        parse_mode="HTML",

    )


# ============================================================
# /MYSTATS
# ============================================================

async def mystats(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = (
        update.effective_user.id
    )

    submissions = list(

        submissions_collection.find(

            {
                "user_id":
                    user_id
            }

        )

    )

    submission_ids = [

        str(
            submission["_id"]
        )

        for submission in submissions

    ]

    votes = []

    if submission_ids:

        votes = list(

            votes_collection.find(

                {

                    "submission_id": {

                        "$in":
                            submission_ids

                    }

                }

            )

        )

    total_votes = len(
        votes
    )

    average = (

        sum(
            v["rating"]
            for v in votes
        )
        / total_votes

        if total_votes

        else 0

    )

    await update.message.reply_text(

        "📊 <b>Your Stats</b>\n\n"

        f"📸 Submissions: "
        f"{len(submissions)}\n"

        f"🗳 Total votes received: "
        f"{total_votes}\n"

        f"⭐ Average rating: "
        f"{average:.2f}/5",

        parse_mode="HTML",

    )


# ============================================================
# TEXT ROUTER
# ============================================================

async def handle_text_router(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):

    user_id = (
        update.effective_user.id
    )

    # Instagram input
    if user_id in waiting_for_instagram:

        await handle_instagram(
            update,
            context,
        )

        return

    # WhatsApp input
    if user_id in waiting_for_whatsapp:

        await handle_whatsapp(
            update,
            context,
        )

        return

    # Caption input
    if user_id in waiting_for_caption:

        await handle_caption(
            update,
            context,
        )

        return


# ============================================================
# ERROR HANDLER
# ============================================================

async def error_handler(
    update,
    context,
):

    logger.exception(
        "Unhandled exception:",
        exc_info=context.error,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # Required environment checks
    # --------------------------------------------------------

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN is missing."
        )

    if not MONGODB_URI:

        raise ValueError(
            "MONGODB_URI is missing."
        )

    if not WEBHOOK_SECRET:

        logger.warning(
            "WEBHOOK_SECRET is not set."
        )

    # --------------------------------------------------------
    # Build application
    # --------------------------------------------------------

    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ========================================================
    # COMMANDS
    # ========================================================

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

    # ========================================================
    # CALLBACKS
    # ========================================================

    # Voting
    application.add_handler(

        CallbackQueryHandler(

            handle_vote,

            pattern=r"^rate:"

        )

    )

    # Leaderboard
    application.add_handler(

        CallbackQueryHandler(

            leaderboard_popup,

            pattern=(
                r"^leaderboard_popup$"
            ),

        )

    )

    # Instagram contact
    application.add_handler(

        CallbackQueryHandler(

            premium_contact,

            pattern=r"^instagram:"

        )

    )

    # WhatsApp contact
    application.add_handler(

        CallbackQueryHandler(

            premium_contact,

            pattern=r"^whatsapp:"

        )

    )

    # Instagram yes/no
    application.add_handler(

        CallbackQueryHandler(

            instagram_choice,

            pattern=(
                r"^instagram_(yes|no)$"
            ),

        )

    )

    # WhatsApp yes/no
    application.add_handler(

        CallbackQueryHandler(

            whatsapp_choice,

            pattern=(
                r"^whatsapp_(yes|no)$"
            ),

        )

    )

    # ========================================================
    # PHOTO HANDLER
    # ========================================================

    application.add_handler(

        MessageHandler(

            filters.ChatType.PRIVATE
            & filters.PHOTO,

            handle_photo,

        )

    )

    # ========================================================
    # TEXT HANDLER
    # ========================================================

    application.add_handler(

        MessageHandler(

            filters.ChatType.PRIVATE
            & filters.TEXT
            & ~filters.COMMAND,

            handle_text_router,

        )

    )

    # ========================================================
    # ERROR HANDLER
    # ========================================================

    application.add_error_handler(
        error_handler
    )

    # ========================================================
    # RENDER WEBHOOK
    # ========================================================

    port = int(

        os.environ.get(
            "PORT",
            "10000",
        )

    )

    render_hostname = os.environ.get(
        "RENDER_EXTERNAL_HOSTNAME"
    )

    if render_hostname:

        webhook_url = (

            f"https://"
            f"{render_hostname}"
            f"/telegram"

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
# START BOT
# ============================================================

if __name__ == "__main__":

    main()

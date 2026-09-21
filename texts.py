"""All user-facing bot text in ONE place. Edit this file to change the bot's
voice — no logic here, just words. Restart the bot after editing.

Placeholders in {braces} are filled in by the code; keep them intact.
"""

WELCOME = (
    "🦅 <b>Welcome to Ravens Confessions</b> ✨\n\n"
    "Everything you send here is posted <b>anonymously</b> 🙈 — your name never "
    "appears anywhere.\n\n"
    "Press /confess to share a confession 🤭 (text, photo or GIF)\n"
    "Press /poll to send an anonymous poll to the hall 📊\n"
    "Press /feedback to message the JCRC directly & anonymously 📨\n\n"
    "Press /rules for the ground rules 📜\n"
    "Press /privacy to see exactly what we store 🔐"
)

CONFESS_INTRO = (
    "💚 <b>Ready to make a confession?</b>\n\n"
    "<b>What happens next:</b>\n"
    "📝 Your message gets posted anonymously to the Raffles Hall confession channel\n"
    "🤖 An AI moderator screens it first (targeted harassment & doxxing get blocked)\n"
    "💬 Fellow hall members can read and reply using comments\n"
    "📸 You can also send a photo with a caption\n"
    "🔒 Your identity stays completely private\n\n"
    "Send your confession now, or /cancel."
)

# The channel post template. {cat}=category, {title}=AI title, {code}=ID, {body}=text
CONFESSION_TEMPLATE = (
    "#{cat} 📝: <b><i>{title}</i></b>\n\n"
    "<b>ID:</b> #{code}\n"
    "---\n{body}\n---\n\n"
    "💬 Reply or discuss below ⬇️"
)

RULES = (
    "📜 <b>Ground rules</b>\n"
    "1. No targeted harassment, rumours, or insults about named individuals.\n"
    "2. No doxxing — phone numbers, room numbers, personal details.\n"
    "3. No hate speech of any kind.\n"
    "4. No NSFW content about identifiable people.\n"
    "5. AI screens every post; humans review borderline cases.\n"
    "6. {strikes} strikes = ban.\n"
)

PRIVACY = (
    "🔐 <b>What we store</b>\n"
    "• A one-way cryptographic hash of your account — never your ID, username, or name.\n"
    "• Author links on posts are <b>permanently erased after {hash_ttl} days</b> — after "
    "that, nobody can connect a post to anyone, ever.\n"
    "• For 'Chat with confessor' and JCRC feedback, an <i>encrypted</i> routing token, "
    "auto-deleted after {chat_ttl} days.\n"
    "• Admins can ban an author without ever learning who they are.\n"
    "We never log message content together with your identity."
)

SUPPORT_NOTE = (
    "\n\n💙 It sounds like you might be going through a rough patch. You're not "
    "alone — you can reach the Samaritans of Singapore at 1767 (24h), or NUS "
    "Counselling & Psychological Services. Your confession was still handled below."
)

POLL_INTRO = (
    "📊 <b>Anonymous poll</b>\n\n"
    "Tap the attach button (📎) and choose <b>Poll</b> — write your question and "
    "options there, then send it to me.\n\n"
    "📸 Want a picture with it? Send the photo/GIF <i>first</i>, then the poll — "
    "it'll sit right above.\n\n"
    "/cancel to abort."
)

FEEDBACK_INTRO = (
    "📨 <b>JCRC Direct Line</b>\n\n"
    "Send your feedback, question, idea, or complaint. It goes straight to the JCRC — "
    "<b>anonymously</b>. They can reply to you through the bot without ever knowing who "
    "you are. /cancel to abort."
)

# Digest personality — this is the LLM prompt that writes the 8am blast.
DIGEST_VOICE = (
    "You write a short, upbeat morning digest for Raffles Hall (NUS) residents. "
    "Rewrite the provided bullet material into a punchy Telegram post. Keep every fact "
    "exactly as given — do not invent scores, names, times or events. Use a few emoji, "
    "short lines, and section headers. Max ~180 words. Output the post only."
)

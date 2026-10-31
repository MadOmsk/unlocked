# Microsoft OAuth callback page (bot/web/oauth.py) — plain browser HTML, no
# aiogram I18nContext at all (this runs inside aiohttp, not an update
# handler), resolved via bot.i18n.gettext. One Fluent key per full
# title/text pair rather than per HTML tag — the markup itself (just the
# outer <h1>/<p> template in oauth.py) stays in Python; only the actual
# copy lives here.
# OAuth result pages
oauth-cancelled-title = Sign-in cancelled
oauth-cancelled-text = You can close this tab and try again from the bot.
oauth-missing-title = Something's missing
oauth-missing-text = Open the link from the bot again.
oauth-token-exchange-failed-title = Microsoft didn't return a token
oauth-token-exchange-failed-text = Go back to the bot and press “Connect XBOX” again.
oauth-failed-title = That didn't work
oauth-success-title = All set, { $gamertag }
oauth-success-text = Head back to Telegram — everything else happens there.

# Proving a Steam account (#169).
oauth-steam-done-title = All set, { $name }
oauth-steam-done-text = Steam is connected. You can go back to the app.
oauth-steam-expired = This link has expired. Start the check again.
oauth-steam-refused = Steam did not confirm the sign-in. Try again.
oauth-steam-unavailable = Steam is not answering right now. Try later.
oauth-steam-already-linked = You have another Steam account connected. Disconnect it first.
oauth-steam-private = The profile is private. Make it public and try again.
oauth-steam-cooldown = This account was unlinked recently. It can be connected again a little later.
oauth-steam-not-configured = Connecting Steam is not set up yet.

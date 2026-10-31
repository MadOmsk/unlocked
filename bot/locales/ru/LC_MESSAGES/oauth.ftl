# Microsoft OAuth callback page (bot/web/oauth.py) — plain browser HTML, no
# aiogram I18nContext at all (this runs inside aiohttp, not an update
# handler), resolved via bot.i18n.gettext. One Fluent key per full
# title/text pair rather than per HTML tag — the markup itself (just the
# outer <h1>/<p> template in oauth.py) stays in Python; only the actual
# Russian copy moves here.
# OAuth result pages
oauth-cancelled-title = Вход отменён
oauth-cancelled-text = Можно закрыть вкладку и попробовать снова в боте.
oauth-missing-title = Чего-то не хватает
oauth-missing-text = Открой ссылку из бота заново.
oauth-token-exchange-failed-title = Microsoft не отдал токен
oauth-token-exchange-failed-text = Вернись в бот и нажми «Подключить XBOX» ещё раз.
oauth-failed-title = Не получилось
oauth-success-title = Готово, { $gamertag }
oauth-success-text = Возвращайся в Telegram — там всё остальное.

# Подтверждение Steam (#169).
oauth-steam-done-title = Готово, { $name }
oauth-steam-done-text = Steam подключён. Можно вернуться в приложение.
oauth-steam-expired = Ссылка устарела. Начни подтверждение заново.
oauth-steam-refused = Steam не подтвердил вход. Попробуй ещё раз.
oauth-steam-unavailable = Steam сейчас не отвечает. Попробуй позже.
oauth-steam-already-linked = У тебя подключён другой аккаунт Steam. Сначала отключи его.
oauth-steam-private = Профиль закрыт. Открой его для всех и попробуй снова.
oauth-steam-cooldown = Этот аккаунт недавно отвязывали. Подключить его снова можно чуть позже.
oauth-steam-not-configured = Подключение Steam пока не настроено.

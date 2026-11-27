"""Extension instances, created once and bound to the app in create_app().

Keeping them here (not in __init__.py) avoids circular imports: models and
services import `db` from this file without importing the whole app.
"""
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import MetaData

# Predictable constraint names. Alembic needs names to alter or drop
# constraints later, and the attendance service will recognise the
# "one mark per session" violation by its name.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

db = SQLAlchemy(metadata=MetaData(naming_convention=NAMING_CONVENTION))
migrate = Migrate()
csrf = CSRFProtect()

login_manager = LoginManager()
login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"
# "basic", not "strong": "strong" logs the user out whenever their IP changes,
# and phones switch between Wi-Fi and mobile data all the time.
login_manager.session_protection = "basic"

# Default key is the client IP. Login and scan limits (Modules 1 and 4) will use
# custom keys, because a whole classroom shares one college NAT IP.
limiter = Limiter(key_func=get_remote_address, default_limits=[])

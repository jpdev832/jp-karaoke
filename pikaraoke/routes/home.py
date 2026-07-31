"""Home page route."""

import flask_babel
from flask import render_template
from flask_smorest import Blueprint

from pikaraoke.lib import autotune_control as at
from pikaraoke.lib.current_app import get_karaoke_instance, get_site_name, is_admin

_ = flask_babel.gettext


home_bp = Blueprint("home", __name__)


@home_bp.route("/")
def home():
    """Home page with now playing info and controls."""
    k = get_karaoke_instance()
    site_name = get_site_name()
    state = at.get_state().to_dict()
    meta = at.meta_payload()
    return render_template(
        "home.html",
        site_title=site_name,
        title="Home",
        transpose_value=k.playback_controller.now_playing_transpose,
        admin=is_admin(),
        is_transpose_enabled=k.is_transpose_enabled,
        volume=k.volume,
        mic_available=k.sound_manager.available,
        autotune=state,
        keys=meta["keys"],
        scales=meta["scales"],
        engine=meta["engine"],
    )

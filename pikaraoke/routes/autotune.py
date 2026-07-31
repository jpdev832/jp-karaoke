"""Auto-Tune REST API and admin panel page."""

from __future__ import annotations

from flask import jsonify, render_template, request
from flask_smorest import Blueprint

from pikaraoke.lib import autotune_control as at
from pikaraoke.lib.current_app import get_site_name, is_admin

autotune_bp = Blueprint("autotune", __name__, url_prefix="/api/autotune")
autotune_ui_bp = Blueprint("autotune_ui", __name__)


@autotune_bp.route("/config", methods=["GET"])
def get_autotune_config():
    return jsonify(
        {
            "ok": True,
            "params": at.get_state().to_dict(),
            "meta": at.meta_payload(),
        }
    )


@autotune_bp.route("/config", methods=["POST", "PUT"])
def set_autotune_config():
    data = request.get_json(silent=True) or {}
    updates = data.get("params", data)
    try:
        params = at.update_live(updates)
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "params": params.to_dict()})


@autotune_ui_bp.route("/autotune")
def autotune_panel_page():
    """Host / admin live Auto-Tune controls."""
    state = at.get_state().to_dict()
    meta = at.meta_payload()
    return render_template(
        "autotune.html",
        site_title=get_site_name(),
        title="Auto-Tune",
        autotune=state,
        keys=meta["keys"],
        scales=meta["scales"],
        engine=meta["engine"],
        admin=is_admin(),
    )

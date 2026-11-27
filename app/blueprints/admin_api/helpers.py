"""Small helpers shared by the admin API routes."""
from flask import g, jsonify, request

from ...schemas.common import load_or_fail


def body(schema):
    """Validate the JSON body. Missing or malformed JSON gives a clear 422."""
    return load_or_fail(schema, request.get_json(silent=True))


def query(schema):
    """Validate URL query parameters."""
    return load_or_fail(schema, request.args.to_dict())


def admin():
    return g.admin


def page_of(items, total, filters, out_schema):
    return jsonify(
        items=out_schema.dump(items, many=True),
        total=total,
        page=filters.get("page", 1),
        per_page=filters.get("per_page", total),
    )
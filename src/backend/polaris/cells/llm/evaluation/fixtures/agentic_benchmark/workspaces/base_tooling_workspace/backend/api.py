"""Backend API fixture."""

# TODO: replace local memory store with persistent adapter


def health() -> dict[str, str]:
    return {"status": "ok"}


def get_user(user_id: str) -> dict[str, str]:
    # TODO: validate user_id format
    return {"id": user_id, "name": "fixture-user"}

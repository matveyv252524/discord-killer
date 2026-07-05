from sqlalchemy import func, select
from database import Message, Channel, User
from datetime import datetime, timedelta

async def server_stats(db, server_id: int):
    """Return basic analytics for a server."""
    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)

    # Message count per day (last 7 days)
    msg_counts = await db.execute(
        select(
            func.date_trunc("day", Message.created_at).label("day"),
            func.count(Message.id).label("count"),
        )
        .where(Message.channel_id.in_(
            select(Channel.id).where(Channel.server_id == server_id)
        ))
        .where(Message.created_at >= week_ago)
        .group_by("day")
        .order_by("day")
    )
    msgs = [{"day": str(r.day.date()), "count": r.count} for r in msg_counts]

    # Top contributors (by message count)
    top_users = await db.execute(
        select(User.username, func.count(Message.id).label("cnt"))
        .join(Message, Message.author_id == User.id)
        .join(Channel, Channel.id == Message.channel_id)
        .where(Channel.server_id == server_id)
        .group_by(User.id)
        .order_by(func.count(Message.id).desc())
        .limit(5)
    )
    top = [{"username": r.username, "messages": r.cnt} for r in top_users]

    # Daily active users (DAU) for last 7 days
    dau = await db.execute(
        select(
            func.date_trunc("day", Message.created_at).label("day"),
            func.count(func.distinct(Message.author_id)).label("users"),
        )
        .where(Message.channel_id.in_(
            select(Channel.id).where(Channel.server_id == server_id)
        ))
        .where(Message.created_at >= week_ago)
        .group_by("day")
        .order_by("day")
    )
    daily = [{"day": str(r.day.date()), "users": r.users} for r in dau]

    return {"messages_per_day": msgs, "top_contributors": top, "daily_active_users": daily}

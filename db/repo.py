from sqlalchemy import select, func, and_
from db.models import User, Referral
from db.database import get_session
from datetime import datetime
from config import settings

BONUS_PER_FRIEND = settings.bonus_per_friend  # 👈 правильно, просто число



# === Добавление реферала ===
async def add_referral(inviter_id: int, invited_id: int):
    async with get_session() as session:
        # проверяем, не добавлен ли уже
        existing = await session.execute(
            select(Referral).where(Referral.invited_id == invited_id)
        )
        if existing.scalar_one_or_none():
            return
        
        # добавляем реферала
        session.add(Referral(
            inviter_id=inviter_id,
            invited_id=invited_id,
            bonus_awarded=False,
            created_at=datetime.utcnow()
        ))

        # увеличиваем счётчик у пригласившего
        inviter = await session.get(User, inviter_id)
        if inviter:
            inviter.referrals_count += 1
        
        await session.commit()

# === Статистика по рефералам ===
async def get_referral_stats(user_id: int):
    async with get_session() as session:
        total = await session.scalar(
            select(func.count(Referral.id)).where(Referral.inviter_id == user_id)
        )
        paid = await session.scalar(
            select(func.count(Referral.id)).where(
                and_(Referral.inviter_id == user_id, Referral.bonus_awarded.is_(True))
            )
        )
        return total or 0, paid or 0

# === Проверка, есть ли доступные генерации ===
async def has_generations(user_id: int) -> bool:
    async with get_session() as session:
        user = await session.get(User, user_id)
        if not user:
            return False

        trial_left = 0 if user.free_trial_used else 1
        invited_total, invited_paid = await get_referral_stats(user_id)
        referral_bonus = invited_paid * settings.bonus_per_friend
        total_available = int(user.balance) + trial_left + referral_bonus

        return total_available > 0

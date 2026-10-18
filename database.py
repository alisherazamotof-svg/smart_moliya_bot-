import os
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncAttrs
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy import Integer, String, Float, DateTime, Text, select, func

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///finance_bot.db")

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, expire_on_commit=False)

class Base(AsyncAttrs, DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(150), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    type: Mapped[str] = mapped_column(String(20)) # 'income', 'expense', 'debt_given', 'debt_taken', 'debt_repaid'
    amount: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(10), default="so'm")
    category: Mapped[str] = mapped_column(String(50), default="umumiy")
    note: Mapped[str] = mapped_column(Text, nullable=True)
    date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class Debt(Base):
    __tablename__ = "debts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    telegram_id: Mapped[int] = mapped_column(Integer, index=True)
    person: Mapped[str] = mapped_column(String(100))
    amount: Mapped[float] = mapped_column(Float)
    debt_type: Mapped[str] = mapped_column(String(20)) # 'berdim' (lent), 'oldim' (borrowed)
    status: Mapped[str] = mapped_column(String(20), default="faol") # 'faol', 'yopilgan'
    date: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def add_transaction(telegram_id: int, t_type: str, amount: float, category: str, note: str, date: datetime = None):
    async with async_session() as session:
        tx = Transaction(
            telegram_id=telegram_id,
            type=t_type,
            amount=amount,
            category=category,
            note=note,
            date=date or datetime.utcnow()
        )
        session.add(tx)
        await session.commit()
        return tx.id

async def get_balance(telegram_id: int):
    async with async_session() as session:
        income_res = await session.execute(
            select(func.sum(Transaction.amount)).where(
                Transaction.telegram_id == telegram_id,
                Transaction.type == 'income'
            )
        )
        total_income = income_res.scalar() or 0.0

        expense_res = await session.execute(
            select(func.sum(Transaction.amount)).where(
                Transaction.telegram_id == telegram_id,
                Transaction.type == 'expense'
            )
        )
        total_expense = expense_res.scalar() or 0.0
        return total_income - total_expense, total_income, total_expense

async def get_recent_transactions(telegram_id: int, limit: int = 5):
    async with async_session() as session:
        res = await session.execute(
            select(Transaction).where(Transaction.telegram_id == telegram_id)
            .order_by(Transaction.date.desc()).limit(limit)
        )
        return res.scalars().all()

async def delete_transaction_by_id(tx_id: int, telegram_id: int):
    async with async_session() as session:
        res = await session.execute(
            select(Transaction).where(Transaction.id == tx_id, Transaction.telegram_id == telegram_id)
        )
        tx = res.scalar_one_or_none()
        if tx:
            await session.delete(tx)
            await session.commit()
            return True
        return False

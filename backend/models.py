from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)

from backend.db import Base


# UTC, but naive: SQLite stores no offset and always reads back naive, so writing an
# aware value would make stored and read timestamps different types
def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Profile(Base):
    __tablename__ = "profile"
    id = Column(Integer, primary_key=True)
    lat = Column(Float)
    lon = Column(Float)
    display_address = Column(String)


class Item(Base):
    __tablename__ = "items"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    category = Column(String)
    criteria_json = Column(String)               # full structured criteria object, serialized
    budget_max = Column(Float)
    target_price = Column(Float)
    fulfillment_preference = Column(String)      # pickup | shipping | either
    radius_miles = Column(Integer)
    min_review_count = Column(Integer)
    status = Column(String)                      # watching | archived
    created_at = Column(DateTime, default=utcnow)


class Listing(Base):
    __tablename__ = "listings"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    retailer = Column(String)                    # bestbuy | target | amazon
    store_id = Column(String)                    # null = online
    store_name = Column(String)
    distance_miles = Column(Float)
    url = Column(String)
    price = Column(Float)
    in_stock = Column(Boolean)
    shipping_days_est = Column(Integer)
    scraped_at = Column(DateTime, default=utcnow)
    __table_args__ = (UniqueConstraint("item_id", "retailer", "store_id", "url"),)


class PriceHistory(Base):
    __tablename__ = "price_history"
    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey("listings.id"))
    price = Column(Float)
    recorded_at = Column(DateTime, default=utcnow)


class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    source = Column(String)                      # amazon | bestbuy | target | reddit | forum | youtube
    rating = Column(Float)
    review_count = Column(Integer)
    verified_ratio = Column(Float)
    rating_distribution_json = Column(String)
    authenticity_flag = Column(String)           # ok | mixed_signal | suspicious_velocity | skewed_distribution
    url = Column(String)
    summary_text = Column(String)
    fetched_at = Column(DateTime, default=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    listing_id = Column(Integer, ForeignKey("listings.id"))
    reason = Column(String)                      # price_drop | target_hit | new_alternative
    sent_at = Column(DateTime)                   # null until included in a digest/immediate email

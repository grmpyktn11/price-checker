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
    # where alerts go. falls back to the USER_EMAIL env var when unset, so an existing
    # install keeps working without touching Settings
    email = Column(String)


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
    # amazon | bestbuy | target | reddit | youtube | <retailer>_inherited.
    # _inherited is a rating attributed from another listing the model judged to be the same
    # product, either one in the same run or one found by searching Amazon for this title
    source = Column(String)
    rating = Column(Float)
    review_count = Column(Integer)
    verified_ratio = Column(Float)               # nothing populates this: no source publishes it
    rating_distribution_json = Column(String)
    # ok | mixed_signal | suspicious_velocity | skewed_distribution. nothing writes
    # suspicious_velocity: it needs a listing age and no source supplies one
    authenticity_flag = Column(String)
    url = Column(String)
    summary_text = Column(String)
    fetched_at = Column(DateTime, default=utcnow)


# the app only ever reads or writes a whole conversation, so JSON columns rather than a
# row per turn. no eviction: rows are tiny and the user wants the history kept
class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(String, primary_key=True)        # client-generated conversation_id
    history_json = Column(String)                # [{"role", "content"}], oldest first
    criteria_json = Column(String)               # set once extraction completes
    # only what /chat/decision needs per product, indexed by product_id, not the whole
    # RankedProduct: the scores are already in the response the client kept
    results_json = Column(String)
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class Alert(Base):
    __tablename__ = "alerts"
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey("items.id"))
    listing_id = Column(Integer, ForeignKey("listings.id"))
    reason = Column(String)                      # price_drop | target_hit | new_alternative
    sent_at = Column(DateTime)                   # null until included in a digest/immediate email


# a shopping list pulled out of a planning conversation. the conversation itself is not kept:
# only what it said to buy, which is the part that stays useful
class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    source = Column(String)                      # paste | share_link
    source_url = Column(String)                  # share_link only
    created_at = Column(DateTime, default=utcnow)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow)


class ProjectItem(Base):
    __tablename__ = "project_items"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    name = Column(String)
    why = Column(String)                         # what the conversation wanted it for
    criteria_json = Column(String)               # normalized, ready for run_pipeline
    quantity = Column(Integer, default=1)
    essential = Column(Boolean, default=True)    # the model's read of must-have vs optional
    selected = Column(Boolean, default=False)    # ticked by the user for the next run
    status = Column(String, default="pending")   # pending | searching | done | failed
    # ProductOut-shaped dicts, as Conversation.results_json holds. run_pipeline persists
    # nothing on its own, so without this a project page is empty after a reload
    results_json = Column(String)
    error = Column(String)                       # why this item failed, shown on the card
    searched_at = Column(DateTime)

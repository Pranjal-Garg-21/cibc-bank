"""Feast entities. ptp_id for promise-level features; crm_customer_id until identity resolution
publishes golden_customer_id (then the customer entity switches, see docs/p3_plan.md §5)."""
from feast import Entity, ValueType

promise = Entity(name="promise", join_keys=["ptp_id"], value_type=ValueType.STRING,
                 description="One promise to pay")
customer = Entity(name="customer", join_keys=["crm_customer_id"], value_type=ValueType.STRING,
                  description="CRM customer (golden_customer_id after identity resolution)")

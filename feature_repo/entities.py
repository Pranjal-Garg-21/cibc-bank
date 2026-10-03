"""Feast entities. ptp_id for promise-level features; the customer is the golden customer from P1's identity
resolution (gold.customer_xref), one person across their CRM, card, loan and deposit records."""
from feast import Entity, ValueType

promise = Entity(name="promise", join_keys=["ptp_id"], value_type=ValueType.STRING,
                 description="One promise to pay")
customer = Entity(name="customer", join_keys=["golden_customer_id"], value_type=ValueType.STRING,
                  description="Golden customer: one person across all source systems (gold.customer_xref)")

# Supplier lead time

Supplier lead time is the elapsed time between a purchase order being placed
and the order being received at the warehouse. Reporting uses received orders
only and groups the result by warehouse and quarter.

Shipment-event timestamps are operational signals, not the governed measure:
partial shipments and retries can make them look shorter than the actual order
lead time.

This guidance is a demo fixture, not an SLA or procurement policy.

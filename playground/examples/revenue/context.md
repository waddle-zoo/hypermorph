# Recognized revenue

Recognized revenue is the sum of gross amount less tax for orders that have
reached the `completed` status, attributed to the order date rather than the
payment date.

Regional reporting joins the order fact to the customer dimension, which is
the only source that carries region. Test accounts are excluded everywhere.

`raw_payments` looks like a revenue source and is not one: it records payment
captures, so partial captures double-count and non-completed transactions leak
into the total.

This guidance is a demo fixture, not GAAP ASC 606 accounting advice.

# Recurring subscription revenue

Recurring revenue is the normalized monthly amount of active subscription plans,
attributed to the period the plan is active in rather than the date a payment
clears. It excludes one-time charges and usage overages, which belong to the
broader revenue domain, not to this recurring slice of it.

`subscription_revenue` is a subdomain of `revenue`: it governs the recurring
part of the same recognized-revenue picture, so its guidance nests under the
revenue context rather than standing beside it. A question that spans both --
recognized revenue and its recurring component -- resolves the two together and
the governed graph relates them with a `contains` edge.

Deferred recurring balances release on a straight-line schedule; refunds and
credits attributed to recurring charges net against the recognized amount.
Cancelled and downgraded plans are churn, measured against the prior period's
active base.

`stripe_events_raw` is not a recurring-revenue source: raw gateway events
double-count partial captures and mix one-time charges with recurring ones.

This guidance is a demo fixture, not GAAP ASC 606 accounting advice.

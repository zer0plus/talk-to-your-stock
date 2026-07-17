# Web BFF Message Turn Ordering

The Web BFF owns the product-visible order of User and Assistant Messages in a
Thread. It serializes each complete turn per Thread, beginning before the User
Message is persisted and ending after the Assistant Message is persisted. This
keeps the visible Thread order aligned with the order sent to the Agent Service.
Different Threads are coordinated independently and can be processed in
parallel.

The Agent Service separately serializes ADK session writes per Thread. That
second guard protects the Agent's durable session from concurrent or direct
calls; it does not define the Web BFF's product Message order.

## Deployment Constraint

The current coordinators are process-local. The PRD #10 local stack therefore
supports one Web BFF worker and one Agent Service worker. Multiple workers or
replicas could acquire different in-memory locks and must not be used until
cross-process Thread ordering is implemented.

Before scaling either service horizontally, replace process-local ordering with
a durable per-Thread sequence and an ordered queue/outbox, or an equivalent
cross-process coordinator. That future design must define delivery guarantees,
retry and failure behavior, and whether posting a Message remains synchronous.
No queue technology or expanded contract is selected by the current local-only
implementation.

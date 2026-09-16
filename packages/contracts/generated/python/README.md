# Python contracts

Python services import the source package directly:
`from sta_contracts.models import ...`.
No generated copy is needed because the Pydantic models *are* the source of truth (ADR-0001).

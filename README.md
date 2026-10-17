# FinEx

FinEx is a business-management application for small businesses.

## Project structure

```text
assets/        Static images and other visual assets
css/           Shared styling
db/migrations/ Versioned database changes
docs/          Architecture decisions, contracts, and operational guides
js/            Browser application code
pages/         Additional user-interface pages
```

## Team working agreement

Financial and inventory changes must pass through the posting service. Organisation isolation, integrations, and AI proposals are separate from that service; the user interface displays backend-provided figures without recalculating them.

Read the [contribution guide](CONTRIBUTING.md) before starting a feature. The key domain boundaries are in [ownership and boundaries](docs/architecture/ownership-and-boundaries.md), and Person A's integration contract is in the [posting-service contract](docs/architecture/posting-service-contract.md).

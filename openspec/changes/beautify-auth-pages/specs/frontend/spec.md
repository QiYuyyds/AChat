# Spec Delta: Frontend

## MODIFIED Requirements

### Requirement: Frontend SHALL provide login and register pages

AChat MUST provide `/login` and `/register` pages accessible without authentication. The login page MUST accept email and password. The register page MUST accept email, name, and password (>= 6 chars).

Both pages MUST use a split-screen layout on `lg` (>= 1024px) and wider viewports:
- **Left panel** (brand showcase): MUST display the product name "AChat", a tagline, and use `--primary` as the background base color with a warm glow overlay using `--warning`. The left panel MUST be hidden on viewports below `lg`.
- **Right panel** (form area): MUST display the auth form inside a card with `backdrop-blur` effect, `shadow-md`, and `inset-hi` highlight. The background MUST use layered `radial-gradient` using `--primary` and `--warning` at low opacity (<= 5%) over `--background`.

On viewports below `lg`, the layout MUST collapse to a single-column form centered on the gradient background, with the left brand panel hidden.

The login and register pages MUST share a common `AuthBrandPanel` component for the left panel to ensure visual consistency.

#### Scenario: User navigates to login
- **WHEN** an unauthenticated user visits any page
- **THEN** they are redirected to `/login`.
- **AND** the login page renders a split-screen layout with brand panel on the left and form card on the right.

#### Scenario: User navigates to login on mobile
- **WHEN** an unauthenticated user visits `/login` on a viewport narrower than 1024px
- **THEN** the left brand panel is hidden
- **AND** the form card is centered on the gradient background.

#### Scenario: User registers
- **WHEN** a user fills the register form and submits
- **THEN** the frontend calls `/api/auth/register`
- **AND** on success, stores the user in AuthStore and redirects to the main workspace.

#### Scenario: Registration is disabled
- **WHEN** `allowRegistration` is false
- **THEN** the register page displays a "registration closed" message inside the split-screen layout
- **AND** the left brand panel remains visible on desktop viewports.

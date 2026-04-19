# Bank Account API Market Research

This document summarizes the API availability and access requirements for various banking institutions to pull balances and transactions.

## Summary Table

| Bank | API Type | Personal Access | Key Requirements | Refresh Frequency |
| :--- | :--- | :--- | :--- | :--- |
| **Monzo** | Personal Developer API | **Direct** | OAuth 2.0, Mobile App Approval | No strict limit (fair use) |
| **PayPal** | Reporting REST API | **Direct** (w/ Business acc) | Client ID/Secret, OAuth 2.0 | Dynamic (50-100 req/min) |
| **Nationwide** | Open Banking (PSD2) | Via Aggregator | TPP Status or Aggregator (Plaid, etc.) | 4 pulls/day (unattended) |
| **HSBC** | Open Banking (PSD2) | Via Aggregator | TPP Status or Aggregator | 4 pulls/day (unattended) |
| **Chase** | Open Banking (PSD2) | Via Aggregator | TPP Status or Aggregator | 4 pulls/day (unattended) |
| **Revolut** | Open Banking (PSD2) | Via Aggregator | TPP Status or Aggregator | 4 pulls/day (unattended) |
| **Metro Bank** | Open Banking (PSD2) | Via Aggregator | TPP Status or Aggregator | 4 pulls/day (unattended) |
| **Tide** | Open Banking (PSD2) | Via Aggregator | TPP Status or Aggregator | 4 pulls/day (unattended) |
| **Marcus** | Open Banking (PSD2) | Via Aggregator | TPP Status or Aggregator | 4 pulls/day (unattended) |

---

## Detailed Findings

### Monzo
Monzo provides a "Personal Developer" API specifically for individual users to access their own data. It is intended for personal projects rather than public applications.
*   **API Availability**: Yes, supports balance and transaction history.
*   **Access Requirements**: 
    *   Sign in to the [Monzo Developer Portal](https://developers.monzo.com/).
    *   Create an OAuth 2.0 Client to receive `client_id` and `client_secret`.
    *   Manual approval (SCA) required via the Monzo app.
*   **Available Data**: Accounts, Balances (including Pots), and Transactions.
*   **Rate Limits**: Fair use (approx. 1 request per second). 5-minute window for full history; otherwise limited to 90 days without re-auth.

### PayPal
PayPal provides the Transaction Search API for retrieving account activity.
*   **API Availability**: Reporting REST APIs for balance and transactions.
*   **Access Requirements**: 
    *   Requires a **Business Account** (free upgrade for personal users).
    *   Generate Client ID and Secret in the [PayPal Developer Dashboard](https://developer.paypal.com/).
*   **Available Data**: Balances (total, available, withheld) and Transactions (payments, refunds, fees). Data retention for 3 years.
*   **Rate Limits**: Dynamic throttling; approx. 50–100 requests per minute observed.

### Nationwide (UK)
Nationwide adheres to the UK Open Banking Standard (PSD2).
*   **API Availability**: Account and Transaction API for balances and transaction history.
*   **Access Requirements**: Restricted to regulated Third-Party Providers (TPPs). Personal users must use an aggregator like Plaid or TrueLayer.
*   **Available Data**: Balances and transaction history (up to 15 months).
*   **Rate Limits**: Maximum 4 requests per 24 hours for unattended access.

### HSBC (UK)
HSBC provides API access via Open Banking infrastructure.
*   **API Availability**: OBIE-compliant APIs for balances and transactions.
*   **Access Requirements**: Requires regulated TPP status and eIDAS certificates. Individuals must use an aggregator.
*   **Available Data**: Account details, real-time balances, and transaction history.
*   **Rate Limits**: 4 data pulls per day for background access.

### Chase (UK)
Chase UK provides programmatic access via Open Banking UK (OBIE) standards.
*   **API Availability**: Open Banking AIS (Account Information Services).
*   **Access Requirements**: FCA-regulated TPP status required. Individuals must use a regulated aggregator app.
*   **Available Data**: Real-time balances and transaction history (90 days without re-auth).
*   **Rate Limits**: 4 times per 24 hours for unattended access.

### Revolut
Revolut does not offer a direct API for personal accounts; only Open Banking is available.
*   **API Availability**: Open Banking (PSD2) API.
*   **Access Requirements**: Requires TPP status or a third-party aggregator (Plaid, TrueLayer).
*   **Available Data**: Balances, transactions, and account metadata.
*   **Rate Limits**: 4 requests per day for background pulls.

### Metro Bank (UK)
Metro Bank provides API access through the UK's Open Banking framework.
*   **API Availability**: Account Information Services (AIS) via Modified Customer Interface (MCI).
*   **Access Requirements**: Regulated TPP status or use of an aggregator (Plaid, GoCardless).
*   **Available Data**: Balances, transactions (up to 730 days), and scheduled payments.
*   **Rate Limits**: 4 background refreshes per day.

### Tide
Tide is business-focused and uses the UK Open Banking Standard.
*   **API Availability**: AIS API for real-time account data.
*   **Access Requirements**: Regulated TPP status or use of aggregators (TrueLayer, Plaid).
*   **Available Data**: Balances, transaction history, and beneficiaries.
*   **Rate Limits**: 4 times per day for background access.

### Marcus by Goldman Sachs (UK)
Marcus UK provides an API for personal savings accounts via the UK Open Banking framework.
*   **API Availability**: Open Banking AIS compliant with PSD2.
*   **Access Requirements**: Regulated TPP status or use of an aggregator (TrueLayer, Yapily).
*   **Available Data**: Balances, transactions (deposits/withdrawals/interest), and account details.
*   **Rate Limits**: 4 background refreshes per day.

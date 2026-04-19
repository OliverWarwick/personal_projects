# Credit Card / Account API Research: Amex and Yonder

Scope: whether a cardholder can access transactions, balances, and other account data through an API or third-party access flow, plus how often that data is likely to refresh.

## Bottom line

- **Amex:** has an official API ecosystem, but the consumer-card data path is **not a public self-serve cardholder API**. The practical route is through an authorized third-party financial service provider that has onboarded to Amex’s open-banking/data-sharing flow.
- **Yonder:** I found **no public developer API** for cardholders. The official route is the Yonder app plus **third-party providers authorized as TPPs** to access the same online account information.

## Amex

### What you can access

- Amex’s open-banking page says an authorized financial service provider can access permissioned American Express financial data through an internally developed Amex API.
- The page explicitly mentions use cases like spending tracking and linking to financial services, which implies transaction-level financial data is intended to be shareable when a provider is onboarded.
- Amex’s UK privacy statement says open banking can be used for income checks and verification, and for consolidated information on payment accounts held elsewhere.

### How access works

- Access is consent-based and happens through an Amex-hosted flow after a financial service provider has onboarded to the Amex API.
- Amex’s developer portal terms indicate API access is for approved developers/apps, not direct cardholder self-service.

### Refresh frequency / limits

- I did **not** find a published consumer-card refresh interval.
- I did find a developer-terms statement that live access is granted per application and API call volume may be limited at Amex’s discretion.

### Other notes

- The official material I reviewed does **not** publish a consumer-card endpoint list or a machine-readable schema for balances, transactions, or metadata.
- For an aggregation app, the practical question is whether your chosen aggregator has Amex coverage for your specific card product.

Sources:
- [Amex Open Banking API overview](https://www.americanexpress.com/en-us/company/open-banking/)
- [Amex Developers Portal Terms](https://a4dextsvr.americanexpress.com/dev-portal/a4d/v1/terms/site)
- [UK Cardmember Privacy Statement](https://www.americanexpress.com/en-gb/company/legal/privacy-centre/cardmember-privacy-statement/)

## Yonder

### What you can access

- Yonder’s Account Terms say that if you authorize a third-party provider, Yonder will give that provider access to the same account information you can access online.
- The Account Terms also say Yonder sends monthly statements in the app, and those statements include details on your activity for the month.
- Public product pages show in-app spend breakdowns, real-time notifications, card freeze/replace, and rewards-related information.

### How access works

- I found no public Yonder developer portal or cardholder API documentation.
- The published access model is the Yonder app plus TPP access under open-banking style permissions.

### Refresh frequency / limits

- I did **not** find any published refresh frequency for transactions or balances.
- I did **not** find any published rate limits.

### Other notes

- The official terms do not enumerate a machine-readable data schema or specific endpoint coverage.
- Yonder’s published terms imply that a TPP should receive whatever online account data the user can view, but not necessarily via a Yonder-owned public API.

Sources:
- [Yonder Account Terms 1.1](https://www.yonder.com/docs/account-terms-1-1)
- [Yonder Homepage](https://www.yonder.com/)
- [Yonder Rewards / product pages](https://www.yonder.com/memberships)

## Practical recommendation for an expense aggregation build

- Start with an aggregator that already supports **Amex + Yonder** and test the live connection for:
  - transactions
  - balance
  - pending/posted status
  - merchant enrichment
  - statement history
- Expect **Amex** to be more dependent on the aggregator’s specific Amex onboarding and card-product support.
- Expect **Yonder** to be available through TPP/open-banking style access, but with less public detail about the exact data contract.

## Best-fit third-party options for a once-daily sync

- **GoCardless Bank Account Data**
  - Best fit for a small or personal project because it has an explicit free tier and clear data-refresh messaging.
  - The public docs say it can return account info, balances, and transaction data, with refreshes up to 4x per day and 720 days of data.
  - Pricing is published: free tier up to 50 requisitions per month, then pay-as-you-go.
- **Tink**
  - Good UK/EU coverage and supports current, savings, and credit card accounts.
  - Pricing is sales-led. The pricing page says new prospects should contact sales and that the listed prices are for existing customers.
  - Supports on-demand and background refresh.
- **Yapily**
  - Strong enterprise option with UK/EU coverage and clear data API scope for balances, transactions, and account details.
  - Pricing is custom/tailored and sales-led.
- **Plaid**
  - Technically viable, but for Europe/UK the pricing model is custom-only, so it is usually less attractive for a small personal build.

### Recommended starting point

- For a once-daily personal-finance sync, start with **GoCardless** first.
- If Amex or Yonder coverage is weak for your exact card product, test **Tink** or **Yapily** next.
- I would treat **Plaid** as a fallback unless you already know you want its broader product set.

### How the integration would work

- User connects an account through the provider’s hosted consent flow.
- The provider returns normalized account, balance, and transaction data.
- Your app stores the latest snapshot and refreshes once daily.
- For this use case, you do not need high-frequency polling or payment initiation features.

## Research gaps

- I did not find a public, documented consumer API for either brand that you can call directly as a cardholder.
- I did not find official published refresh timings or rate limits for either brand’s consumer data-sharing path.
- I did not verify aggregator-specific coverage, because that depends on the provider you choose.

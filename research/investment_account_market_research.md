# Investment Account API Research

Scope: whether each provider exposes APIs that can be used to pull transactions, current portfolio/holdings, balances, and related data for an aggregated view of income, expenses, and outgoings.

## Bottom line

- **Best direct fit:** Interactive Brokers and Coinbase both expose usable retail APIs.
- **Partial fit:** Hargreaves Lansdown and AJ Bell expose some API/open-banking capability, but the public material I found is limited and mostly partner-oriented.
- **Weak fit for direct API aggregation:** Scottish Widows and Aviva do not appear to offer a public retail API program; Aviva has a partner portal, and Scottish Widows appears to rely on app/portal access plus partner/open-finance integrations.

## Provider summary

| Provider | Retail API access | Data you can pull | Refresh / limits | Fit for aggregation |
| --- | --- | --- | --- | --- |
| Interactive Brokers | Yes, direct via IBKR APIs | Accounts, positions, cash balances, activity, performance, transactions, market data | Web API limit 10 req/sec per authenticated username/session; endpoint-specific throttles; activity endpoints around 1 req/15 mins | Strong |
| Hargreaves Lansdown | No general-purpose public investment API found; Active Savings cash hub only | Account list, balances, transactions, product metadata for Active Savings cash hub | PSD2/AISP access up to 4 times/day; tokens are time-limited and refreshed | Partial |
| AJ Bell | No public retail API found; developer/partner route only | Summary balances, portfolios, transaction histories mentioned in partner/open-banking context | No public rate limits or refresh intervals found | Partial |
| Coinbase | Yes, OAuth2-based retail API | User/account data, balances, transactions, addresses, fiat deposit/withdrawal flows | 10,000 requests/hour per OAuth-authenticated user or API key; access tokens last 1 hour; refresh tokens available with `offline_access` | Strong |
| Scottish Widows | No public retail or partner API found | Portal/app data includes pension value, contributions, transactions/investments, annual statements | No public API cadence or rate limits found | Weak |
| Aviva | Partner API portal only | Pensions APIs mention policy/plan enquiry, group scheme enquiry, contributions, billing, document generation | No public refresh cadence or rate limits found | Weak |

## Interactive Brokers

### Access model

- IBKR exposes direct API access for retail customers through its public API family: Web API, TWS API, Excel API, and FIX.
- The Web API docs show OAuth2-based access for the current web surface.

### Data available

- Account list and account access.
- Portfolio summaries and account reporting.
- Cash balances and ledger by currency.
- Positions and portfolio endpoints.
- Activity, performance, and transaction endpoints.
- Market data, including snapshots, streaming, and historical data.

### Limits and cadence

- Global Web API limit: 10 requests/sec per authenticated username/session.
- Endpoint-specific limits exist, including:
  - `/iserver/marketdata/snapshot`: 10 req/sec
  - `/portfolio/accounts`: 1 req/5 secs
  - `/portfolio/subaccounts`: 1 req/5 secs
  - `/pa/*`: 1 req/15 mins
  - `/iserver/marketdata/history`: 5 concurrent requests
- Market data lines are limited by entitlement and start at 100 lines by default.

### Notes for your use case

- IBKR is the strongest fit if you want direct portfolio, cash balance, and transaction aggregation.
- I did not find a clearly published statement or tax-lot endpoint in the current Web API docs I reviewed.

Sources:
- [IBKR API overview](https://www.interactivebrokers.com/campus/api)
- [IBKR Web API docs](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/)
- [IBKR getting started](https://www.interactivebrokers.com/campus/ibkr-api-page/getting-started/)

## Hargreaves Lansdown

### Access model

- I did not find a general-purpose public investment API for retail customers.
- HL says it only offers open application programming interfaces for the cash hub account of its Active Savings service.
- The API summary indicates access is via Open Banking / third-party provider registration, not direct retail developer access.

### Data available

- Active Savings API:
  - account list
  - balances
  - transactions
  - product metadata
- The public summary explicitly says statements, payment initiation, confirmation of funds, and several bulk services are not provided.

### Limits and cadence

- HL states AISP access to account information is allowed up to 4 times per day under PSD2.
- Access tokens are time-limited and refreshed via refresh token.
- HL publishes Open Banking performance statistics every three months.

### Notes for your use case

- The public API coverage is for **Active Savings cash hub only**, not HL investment accounts or portfolio holdings.
- For an aggregated view of investments, HL looks like a gap unless you use another provider or a third-party aggregator with a different integration path.

Sources:
- [HL service information](https://www.hl.co.uk/savings/savings-account/service-information)
- [HL Open Banking API summary PDF](https://www.hl.co.uk/__data/assets/pdf_file/0009/14616729/Open-Banking-API-Summary.pdf)

## AJ Bell

### Access model

- I did not find a public retail API for direct customer use.
- AJ Bell published a Developer Hub and open-banking style partner route for third parties.

### Data available

- AJ Bell’s public material mentions:
  - summary balances
  - portfolios
  - transaction histories
- The public security material also references cash hub flows and open banking use in payment context.

### Limits and cadence

- I did not find public API rate limits or refresh intervals in the official sources reviewed.

### Notes for your use case

- AJ Bell looks potentially useful through a partner or aggregator route, but the public material is not enough to rely on for a direct self-serve API integration plan.

Sources:
- [AJ Bell Developer Hub launch](https://www.ajbell.co.uk/group/news/aj-bell-launches-developer-hub-link-open-banking)
- [AJ Bell security features](https://www.ajbell.co.uk/security/security-features)

## Coinbase

### Access model

- Coinbase offers consumer-facing API access via OAuth2 on `api.coinbase.com`.
- After a user authorizes your app, you can call account and transaction endpoints with scoped permissions.
- Coinbase states API key auth should only be used for your own account; OAuth2 is required to access other users’ accounts.

### Data available

- User profile endpoint.
- Accounts and balances.
- Account-specific transactions, including list and single-transaction reads.
- Addresses and fiat deposit/withdrawal endpoints if you need movement flows.

### Limits and cadence

- Access tokens expire after 1 hour.
- If `offline_access` is requested, refresh tokens are available and expire after 1.5 years.
- Rate limit is 10,000 requests/hour per API key or OAuth-authenticated user.

### Notes for your use case

- Coinbase is a strong fit for retail crypto holdings and transactions.
- The scope model is explicit, so if you need new data later, expect users to re-authorize for added scopes.

Sources:
- [Coinbase available APIs](https://docs.cdp.coinbase.com/coinbase-app/oauth2-integration/available-apis)
- [Coinbase transactions docs](https://docs.cdp.coinbase.com/coinbase-app/docs/api-transactions)
- [Coinbase OAuth permissions](https://docs.cdp.coinbase.com/coinbase-app/oauth2-integration/oauth2-permissions)
- [Coinbase token handling](https://docs.cdp.coinbase.com/coinbase-app/oauth2-integration/access-and-refresh-tokens)
- [Coinbase rate limiting](https://docs.cdp.coinbase.com/coinbase-app/api-architecture/rate-limiting)
- [Coinbase API key auth](https://docs.cdp.coinbase.com/coinbase-app/authentication-authorization/api-key-authentication)

## Scottish Widows

### Access model

- I did not find a publicly documented retail or partner API program.
- The public-facing material points to app/portal access and a partner open-finance integration with Moneyhub.

### Data available

- Customer portal/app material says users can:
  - see pension value
  - view transactions and investments
  - request annual statements
  - connect other providers’ accounts
  - monitor savings

### Limits and cadence

- I did not find a published API refresh cadence or rate-limit policy.
- The closest public wording is “in real time” in app marketing, but that is not a documented API throttle or sync interval.

### Notes for your use case

- Scottish Widows looks like a gap for direct API integration.
- If you need this provider in an aggregation app, a third-party open-finance aggregator may be the realistic route.

Sources:
- [Scottish Widows homepage](https://www.scottishwidows.co.uk/)
- [Scottish Widows app](https://www.scottishwidows.co.uk/app.html)
- [Scottish Widows open finance blog](https://www.scottishwidows.co.uk/workplace-insights/blogs/open-finance-that-puts-people-in-control.html)
- [Scottish Widows manage your account](https://www.scottishwidows.co.uk/retirement/pension-transfers/apply/retirement/manage-your-account.html)

## Aviva

### Access model

- Aviva has a partner API portal rather than a retail/public API program.
- Only authorised partners may create accounts and consume the APIs.

### Data available

- Public pension API scope mentions:
  - policy plan enquiry
  - group scheme enquiry
  - new members and contributions enquiry
  - group scheme billing enquiry
  - document generation
- That suggests contributions and document retrieval are supported.
- A balance or policy value endpoint is implied by policy/plan enquiry but was not explicitly spelled out in the public summary I reviewed.

### Limits and cadence

- I did not find a public rate-limit policy or scheduled refresh frequency.
- Some portal material mentions availability, but not refresh cadence.

### Notes for your use case

- Aviva looks like a partner integration opportunity, not a direct self-serve retail API.
- If your goal is a personal aggregation tool, Aviva may require an approved integration path or a third-party provider with Aviva coverage.

Sources:
- [Aviva get started](https://developer.aviva.co.uk/get-started)
- [Aviva pensions APIs](https://developer.aviva.co.uk/pensions)
- [Aviva contact page](https://developer.aviva.co.uk/contact)

## Practical recommendation

- For a first version of your aggregated view, start with:
  - **Interactive Brokers** for brokerage holdings, balances, and transactions
  - **Coinbase** for crypto balances and transactions
- Treat:
  - **Hargreaves Lansdown** as partial coverage only, mainly Active Savings cash hub
  - **AJ Bell** as partner-only until you confirm a specific integration path
  - **Scottish Widows** and **Aviva** as likely third-party-aggregator integrations rather than direct APIs

## Ranked vendor matrix

Scored for your specific goal: aggregated view of expenses, incomings, outgoings, holdings, and pension values.

| Vendor | UK pensions | Brokerage / investments | Crypto | Transactions | Balances / holdings | Best route | Rank |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Moneyhub | Yes | Yes | Yes | Yes | Yes | Single vendor aggregator | 1 |
| Plaid | Yes | Yes | No clear public UK crypto aggregation product found | Yes | Yes | Single vendor for investments, hybrid for crypto | 2 |
| Interactive Brokers + Coinbase | No | Yes | Yes | Yes | Yes | Direct APIs, hybrid stack | 3 |
| Moneyhub + Coinbase | Yes | Yes | Yes | Yes | Yes | Hybrid, broadest practical coverage | 4 |
| Hargreaves Lansdown | Limited | Limited | No | Partially | Cash hub only | Third-party/open-banking only | 5 |
| AJ Bell | Limited | Possibly via partner route | No | Possibly via partner route | Possibly via partner route | Partner-only | 6 |
| Aviva | Yes, partner-only | No clear brokerage coverage | No | Contributions/document flows | Policy/plan level only | Partner portal | 7 |
| Scottish Widows | Yes, via Moneyhub/open-finance route | Yes, via Moneyhub/open-finance route | No clear direct path | Yes, via Moneyhub/open-finance route | Yes, via Moneyhub/open-finance route | Moneyhub / open-finance | 8 |

### Interpretation

- **Moneyhub** is the cleanest “one vendor” answer for your full scope.
- **Plaid** is strong for investment holdings and transactions, but I would treat crypto as a separate integration unless you verify a current UK crypto product.
- **Direct APIs** from IBKR and Coinbase are still the most reliable building blocks if you are happy with a hybrid architecture.
- **HL and AJ Bell** are not great primary targets for direct integration because the public API story is too narrow or partner-only.
- **Scottish Widows** is best approached through **Moneyhub**, not directly.
- **Aviva** likely needs a partner integration path; I would not plan around a public self-serve API.

## Research gaps

- I did not find public direct APIs for HL investment accounts, Scottish Widows pensions, or a retail self-serve AJ Bell API.
- I did not find published refresh intervals for Scottish Widows or Aviva.
- For HL and AJ Bell, the public documentation is enough to confirm some access models, but not enough to guarantee full portfolio coverage for your exact accounts.

## Third-party options worth checking

### Moneyhub

- **Scottish Widows:** this is the clearest third-party path I found.
- Moneyhub says Scottish Widows integrated Moneyhub APIs into its app in January 2024 to let users view pensions plus other financial accounts in one place.
- Moneyhub also says Scottish Widows users can connect financial assets from over 70 other providers, which makes Moneyhub the strongest open-finance route for pension aggregation here.
- Moneyhub’s own platform supports periodic polling multiple times a day and open-finance coverage for pensions, investments, and mortgages.

### Hargreaves Lansdown

- HL’s public documentation only confirms open APIs for the **Active Savings cash hub**.
- HL also says it partnered with **Ecospend** for Pay by Bank, but that is a payment flow, not an account/holdings read API.
- For a live aggregation build, HL looks more like a partial coverage provider unless you can use a third-party open-finance product that has a separate agreement or standards-based route.

### TISA OSIP / standards-based routes

- Moneyhub says Hargreaves Lansdown participated in TISA’s Open Savings, Investments and Pensions initiative.
- That suggests a standards-based ecosystem exists for pensions and investments, but the public record I found does not prove current retail availability for your exact HL accounts.
- If you want a vendor shortlist beyond Moneyhub, this is the most relevant standards layer to evaluate next.

### Practical conclusion

- If you want one vendor to explore first for both **Scottish Widows** and broader UK open-finance coverage, start with **Moneyhub**.
- For **HL**, expect limited coverage unless you only need Active Savings cash hub data or a partner-specific integration.

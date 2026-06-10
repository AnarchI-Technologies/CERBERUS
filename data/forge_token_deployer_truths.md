# Cross Forge Token Deployer Truths

Source: `https://www.clawroyale.ai/forge-token-deployer.md`.

## Purpose

- Cross Forge token deployment uses the CrossToken CLI script from `forge-token-deployer.md`.
- The script deploys tokens through Cross Console APIs and can optionally create the Forge liquidity pool.
- Do not scaffold or run `deploy-token.js` until an actual token deployment is requested and parameters are confirmed.

## Auth And Wallet Modes

- Auth modes are `client` and `vendor`.
- `client` requires `CLIENT_KEY` and `CLIENT_SECRET` from RampConsole.
- `vendor` needs no signup or credentials and is the default.
- Wallet modes are `user` and `tmp`.
- `user` makes the provided wallet the token owner and returns an unsigned pool transaction for frontend signing.
- `tmp` creates a temporary wallet, deploys the token, and creates the pool automatically.
- `tmp` owner permissions are not reusable.

## Agent Token Rule

- Agent tokens use category `ai_agent`.
- Agent tokens must use `--wallet=user` with the Agent EOA wallet address.
- Never use `--wallet=tmp` for agent tokens because token `owner()` must equal the Agent EOA.
- Using a temp wallet for an agent token can cause registration failure with 403.

## Defaults And Categories

- Default execution is `--auth=vendor --wallet=tmp`.
- Non-agent game tokens can use the default if the user wants full deploy plus pool creation in one step.
- Valid categories are `game` and `ai_agent`.
- Symbols are globally unique and case-insensitive.

## Runtime Constants

- Cross mainnet RPC is `https://mainnet.crosstoken.io:22001`.
- Forge router is `0x7aF414e4d373bb332f47769c8d28A446A0C1a1E8`.
- Pair token B is `0xDdF8AaA3927b8Fd5684dc2edcc7287EcB0A2122d`.
- Vendor address is `0x254465624da909e0072fbf8c32bcfc26b9fe9da9`.
- Trade links use `https://x.crosstoken.io/forge/token/{tokenAddress}`.

## Required Parameters

- Token name.
- Token symbol.
- Token description.
- Image URL or local PNG/JPG file path.
- Wallet address.
- Category.
- Local image files must be PNG or JPG and at most 1 MB.

## Results

- `--wallet=user` returns `tokenAddress`, `tradeLink`, and `unsignedTx`.
- `--wallet=tmp` returns `poolCreated`, `tokenAddress`, `tradeLink`, `txHash`, and `blockNumber`.
- With `--wallet=user`, the trade link works only after the unsigned pool transaction is signed and succeeds.

## Errors

- Symbol already in use means choose a different unique symbol.
- HTTP 401 means client credentials are invalid or missing.
- HTTP 400 invalid owner means the wallet address is malformed.
- Pool transaction failure may leave the token deployed while pool creation failed.
- Image file exceeds 1 MB means resize or compress before deploying.
- `INSUFFICIENT_OUTPUT_AMOUNT` is likely transient slippage and can be retried.

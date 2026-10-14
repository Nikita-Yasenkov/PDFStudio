# Releasing PDF Studio

## How it works

[`.github/workflows/build.yml`](../.github/workflows/build.yml) runs on every
push to `main`, every pull request, and on demand (**Actions → Build → Run
workflow**):

| Job | What it does |
|---|---|
| **Windows** | Runs the tests, builds `PDFStudio.exe`, runs its self-test, signs it (releases only, see below) and zips it. |
| **macOS (arm64, x86_64)** | Runs the tests, builds `PDF Studio.app` on an Apple silicon and an Intel runner, runs its self-test and puts it in a disk image. |
| **Publish release** | Only on `main`, and only when `__version__` has no release yet: publishes release `v<version>` with all three downloads. |

Every run attaches its downloads (`windows`, `macos-arm64`, `macos-x86_64`) to
the run's summary page for 30 days, so you can try a build before releasing
it. A release only goes out when every job succeeds, so all platforms always
ship together.

## Ship a new version

1. Change `__version__` in [`src/pdf_studio/__init__.py`](../src/pdf_studio/__init__.py).
   Use MAJOR.MINOR.PATCH: bug fixes bump PATCH (0.1.1 → 0.1.2), new features
   bump MINOR (0.1.2 → 0.2.0), changes that break what users rely on bump MAJOR.
2. Add a `## <version>` section to [`CHANGELOG.md`](../CHANGELOG.md), written
   for users. It becomes the release notes, followed by install steps and
   checksums. The workflow stops before building anything if it's missing.
3. Push to `main`, or merge a pull request. Watch it under **Actions**.

If a release run fails, fix the cause and push again. The version still has
no release, so the next run publishes it.

The README's download links point at `releases/latest/download/…`, so they
always serve the newest release without changes.

## Windows code signing (Azure Artifact Signing)

A signature tells Windows who published the app. It keeps Microsoft Defender's
machine-learning false positives in check, and SmartScreen warnings fade as
the signed app builds a reputation. A brand-new certificate can still see
warnings for a short while.

**Requirements and cost.** Individual developers must be in the United States
or Canada (organizations: more countries). It needs a paid Azure subscription
and costs $9.99 a month on the Basic tier, which includes 5,000 signatures.
The pipeline signs one file per release.

### One-time setup

In the [Azure portal](https://portal.azure.com):

1. **Subscription and billing.** For individual identity validation, the
   billing account must have the account type *Individual*, and its legal name
   and sold-to address must match your government ID.
2. **Register the service.** *Subscriptions* → your subscription → *Resource
   providers* → `Microsoft.CodeSigning` → *Register*.
3. **Create the signing account.** Search *Artifact Signing Accounts* →
   *Create*. Choose a region (for example *East US*, endpoint
   `https://eus.codesigning.azure.net/`) and the *Basic* tier. Write down the
   account name and the endpoint.
4. **Verify your identity.** In the account, *Access control (IAM)* → give
   yourself the *Artifact Signing Identity Verifier* role. Then *Identity
   validations* → *Individual* → *New identity* → *Public*, and follow the
   verification steps (photo ID and the Microsoft Authenticator app). This can
   take from minutes to several business days.
5. **Create the certificate profile.** *Certificate profiles* → *Create* →
   *Public Trust*. Select your completed identity validation. Write down the
   profile name.
6. **Let GitHub sign in without a password.** *Microsoft Entra ID* → *App
   registrations* → *New registration* (for example `pdf-studio-github`). In
   the new app: *Certificates & secrets* → *Federated credentials* → *Add
   credential* → *GitHub Actions deploying Azure resources*. Use organization
   `Nikita-Yasenkov`, repository `PDFStudio`, entity *Branch*, branch `main`.
7. **Allow it to sign.** Back in the signing account: *Access control (IAM)*
   → *Add role assignment* → *Artifact Signing Certificate Profile Signer* →
   select the app registration from step 6.

In GitHub, open the repository's **Settings → Secrets and variables → Actions**:

| Kind | Name | Value |
|---|---|---|
| Secret | `AZURE_CLIENT_ID` | App registration → *Application (client) ID* |
| Secret | `AZURE_TENANT_ID` | App registration → *Directory (tenant) ID* |
| Secret | `AZURE_SUBSCRIPTION_ID` | Your subscription's ID |
| Variable | `AZURE_SIGNING_ENDPOINT` | The endpoint from step 3 |
| Variable | `AZURE_SIGNING_ACCOUNT` | The account name from step 3 |
| Variable | `AZURE_CERTIFICATE_PROFILE` | The profile name from step 5 |

From the next release on, `PDFStudio.exe` is signed and timestamped, and the
workflow fails the release if the signature isn't valid. Until
`AZURE_SIGNING_ENDPOINT` is set, releases go out unsigned.

## macOS

The disk images contain an ad-hoc signed app. macOS runs it, but because Apple
hasn't notarized it, people confirm it once: **System Settings → Privacy &
Security → Open Anyway**.

To remove that step, the app has to be signed with a *Developer ID
Application* certificate and notarized by Apple. That needs the Apple
Developer Program (US$99 a year); Azure signing only covers Windows. The
workflow doesn't do this yet.

GitHub's Intel macOS runners (`macos-15-intel`) are supported until fall 2027.
After that, drop the `x86_64` build from the matrix.

## Building locally

```powershell
.\scripts\build_pdf_studio.ps1 -Smoke -Zip       # Windows: dist\PDFStudio-win64.zip
```

```bash
scripts/build_pdf_studio_macos.sh --smoke        # macOS: dist/PDFStudio-macos-<arch>.dmg
```

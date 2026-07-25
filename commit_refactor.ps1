# commit_refactor.ps1

function Run-Step {
    param(
        [string]$StepName,
        [string]$CommitMessage,
        [string[]]$Files,
        [string]$TestPath
    )

    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "Processing Step: $StepName"
    Write-Host "==================================================" -ForegroundColor Cyan

    # Add the specific files for this step
    git add $Files
    Write-Host "Staged files for commit." -ForegroundColor Green

    # Run the specific tests for this step
    Write-Host "Running tests for '$TestPath'..."
    # Use pytest with -v for verbose output
    pytest $TestPath -v
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Tests failed for step '$StepName'. Aborting." -ForegroundColor Red
        exit 1 # Stop the script on test failure
    }
    Write-Host "Tests passed." -ForegroundColor Green

    # Commit the changes
    git commit -m $CommitMessage
    Write-Host "Committed with message: '$CommitMessage'" -ForegroundColor Green
    Write-Host ""
}

# --- Main Script ---

# Step 1: Isolate all strategy modules
$strategyFiles = Get-ChildItem -Path "src/claw_royale/ai" -Recurse | ForEach-Object { $_.FullName }
Run-Step -StepName "Strategy Isolation" `
    -CommitMessage "refactor(ai): Isolate all strategy families into modules" `
    -Files $strategyFiles `
    -TestPath "tests/strategies"

# Step 2: Implement the core decoupled architecture
$coreFiles = @(
    "src/pulse.py",
    "src/events/types.py",
    "src/switch_board/interface.py",
    "src/switch_board/implementations.py"
)
Run-Step -StepName "Core Architecture" `
    -CommitMessage "feat(core): Implement event-driven architecture with Pulse and SwitchBoard" `
    -Files $coreFiles `
    -TestPath "tests/test_pulse.py"

# Step 3: Refactor the adapter and add the main application entrypoint
$adapterFiles = @(
    "src/claw_royale/adapter.py",
    "src/main.py"
)
Run-Step -StepName "Adapter Refactor" `
    -CommitMessage "refactor(adapter): Decouple ClawRoyaleAdapter to use SwitchBoard" `
    -Files $adapterFiles `
    -TestPath "tests/test_adapter.py"

# Step 4: Add and verify the architectural contract tests
$archTestFiles = @(
    "tests/integration/test_layer_independence.py",
    "tests/architecture/test_action_contract.py"
)
Run-Step -StepName "Architectural Verification" `
    -CommitMessage "test(arch): Add integration tests to verify layer independence" `
    -Files $archTestFiles `
    -TestPath "tests" # Run all tests in the final step to be sure

# Final Push
Write-Host "==================================================" -ForegroundColor Cyan
Write-Host "All steps completed successfully. Pushing to remote..."
Write-Host "==================================================" -ForegroundColor Cyan
git push

Write-Host "Done." -ForegroundColor Green

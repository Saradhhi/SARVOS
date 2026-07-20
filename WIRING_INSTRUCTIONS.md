# Wiring the LoopCV/Teal import feature into SARVOS

Two new files go in `agents/`:
- `agents/import_agent.py`
- `agents/import_intent.py`

Then four small edits to existing files:

## 1. core/schemas.py — add IMPORT to the AgentName enum
Find the line:      JOB = "job"
Add after it:       IMPORT = "import"

## 2. core/factory.py — register the agent, passing it the JobAgent
Find where JobAgent is created:
    AgentName.JOB: (job_agent := JobAgent(memory, browser=interactive_browser)),
(if it's not already assigned to `job_agent`, change it so it is — i.e. add
the `(job_agent := ...)` walrus so you can reference it on the next line)

Add after it:
    AgentName.IMPORT: ImportAgent(memory, job=job_agent),

And at the top with the other imports:
    from agents.import_agent import ImportAgent

## 3. agents/planner.py — route import commands to the IMPORT agent
At the top with the other intent imports:
    from agents.import_intent import classify as classify_import, Operation as ImportOp

Then, near where the job intent is checked (BEFORE the document/browser
checks so 'import ... .csv' isn't mistaken for a document read), add:

    import_intent = classify_import(task.instruction)
    if import_intent.operation != ImportOp.UNKNOWN:
        return [Task(
            parent_request_id=task.parent_request_id,
            agent=AgentName.IMPORT,
            instruction=task.instruction,
            context=task.context,
            risk=import_intent.risk,
        )]

## 4. (optional) main.py shell guard
No change needed — 'import applications from x.csv' routes to a specialist,
so the shell guard already leaves it alone.

## Test it
1. Put a CSV in sarvos_workspace/imported/ (create the folder).
2. In SARVOS:
       use candidate alice
       list import files
       import applications from <yourfile>.csv
       list applications

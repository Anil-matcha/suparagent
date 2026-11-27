# Visual Workflows

Suparagent includes a visual workflow canvas for building multi-step agent pipelines, conditional logic, and automated triggers. Workflows allow chaining LLM processing steps, condition nodes, and actions into executable directed graphs with live data validation.

## Overview

The workflow builder provides an interactive infinite canvas with:
- **Node-based visual orchestration**: Connect triggers, LLM prompts, conditional routers, and tools.
- **Strict port type validation**: Enforces type compatibility between node ports (text, image, document, any) with real-time error tooltips.
- **Dockable creative toolbars**: Floating bottom dock with select, pan, pencil draw, sticky notes, geometric shapes, and text annotations.
- **Interactive minimap**: Live spatial overview with viewport tracking and pan controls.
- **Canvas search palette**: Search and jump directly to any step or note on the canvas.

---

## Step and Node Types

### 1. Triggers
Workflows start with an entry trigger that initiates graph execution:
* **Message Trigger**: Starts automatically when a chat message starts with a specific keyword (e.g., `analyze:`, `review:`).
* **Schedule Trigger**: Runs on a recurring cron or interval schedule (e.g., Daily at 9:00 AM, Every Monday).
* **Webhook Trigger**: Exposes an HTTP webhook URL to trigger runs from external systems or scripts.

### 2. LLM Execution Nodes
* **Model Selection**: Choose any configured model engine (Google Gemini, Claude, Codex, OpenCode, or custom ACP).
* **Prompt Templates**: Use `{{input.message}}` and upstream node references to format input prompts.
* **Reasoning Effort**: Configure reasoning effort levels (Low, Medium, High) for models that support deep reasoning.
* **System Prompts**: Optional custom instructions per step.

### 3. Conditions & Logic
* Evaluate upstream step outputs against boolean conditions or regex patterns.
* Branch execution flows based on matching rules (`if true -> Branch A`, `if false -> Branch B`).

---

## Wire & Port Compatibility Rules

Nodes expose typed connection ports along their borders:
* **Text (`T`)**: Strings, prompt responses, markdown summaries.
* **Image (`🖼`)**: Photos, generated charts, image attachments.
* **Document (`📄`)**: PDFs, Word documents, Excel sheets, and parsed attachments.
* **Any (`*`)**: Universal ports accepting any data format.

### Validation Rules:
1. **Type Matching**: A wire connecting an incompatible port (e.g., a `Text` output to an `Image` input) turns red with a dashed warning and shows a disconnect badge `(X)`.
2. **Cycle Prevention**: Workflows cannot contain recursive cycles that would cause infinite loops.
3. **Execution Gating**: Steps with invalid connections cannot be executed until resolved.

---

## Canvas Annotation & Drawing Tools

Workflows support rich visual documentation on the canvas:

### Sticky Notes
* Add sticky notes anywhere on the canvas to document steps and flow logic.
* **Color Themes**: Warm Amber, Emerald Green, Sky Blue, Rose Pink, Violet Purple, and Neutral Slate.
* **Typography Controls**: Font size (14px, 16px, 18px, 24px), font family, text alignment (left, center, right, justify).
* **Smooth Resize**: Curved corner resize handle for dragging to any dimension.

### Geometric Shapes
* Place structural shapes to group or highlight sections of the graph:
  * Rectangle / Box
  * Circle / Ellipse
  * Diamond (Decision block)
  * Triangle & Inverted Triangle
  * Pill / Capsule
  * Star (Highlight / Milestone)

### Freehand Pencil Draw (`P`)
* Toggle the pencil tool to sketch diagrams or annotations directly onto the canvas.
* 8-color quick palette with thickness control and undo support.

---

## Keyboard Shortcuts

| Shortcut | Action |
| :--- | :--- |
| `V` | Select / Pointer tool |
| `H` or `Space + Drag` | Pan canvas |
| `P` | Freehand pencil tool |
| `Delete` / `Backspace` | Delete selected node, wire, or note |
| `Ctrl + +` / `Ctrl + -` | Zoom in / Zoom out |
| `Ctrl + 0` | Reset zoom to 100% |
| `Ctrl + F` | Open canvas node search palette |

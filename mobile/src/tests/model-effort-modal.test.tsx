import { fireEvent, render, screen } from '@testing-library/react-native';

import { ModelEffortModal } from '@/components/model-effort-modal';
import type { Engine } from '@/lib/engines';

const mockEngines: Engine[] = [
  {
    id: 'claude',
    label: 'Claude Code',
    available: true,
    reason: null,
    models: [
      { id: 'opus', label: 'Opus', isDefault: false },
      { id: 'sonnet', label: 'Sonnet 3.7', isDefault: true, supportedReasoningEfforts: ['low', 'medium', 'high'] },
    ],
    defaultModel: 'sonnet',
    customModels: false,
    computers: true,
    limit: null,
  },
  {
    id: 'antigravity',
    label: 'Antigravity',
    available: true,
    reason: null,
    models: [
      { id: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash', isDefault: true },
      { id: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro', isDefault: false, supportedReasoningEfforts: ['minimal', 'low', 'medium', 'high', 'max'] },
    ],
    defaultModel: 'gemini-2.5-flash',
    customModels: false,
    computers: true,
    limit: null,
  },
];

describe('ModelEffortModal', () => {
  it('renders modal with dynamic models and reasoning effort controls', async () => {
    const onSelect = jest.fn();
    const onClose = jest.fn();

    await render(
      <ModelEffortModal
        visible={true}
        onClose={onClose}
        engines={mockEngines}
        currentProvider="claude"
        currentModel="sonnet"
        currentEffort="medium"
        isDraft={true}
        onSelect={onSelect}
      />,
    );

    expect(screen.getByText('Model & Reasoning')).toBeTruthy();
    expect(screen.getByText('Sonnet 3.7')).toBeTruthy();
    expect(screen.getByText('Opus')).toBeTruthy();
    expect(screen.getByTestId('effort-chip-high')).toBeTruthy();
  });

  it('allows switching model and reasoning effort dynamically', async () => {
    const onSelect = jest.fn();
    const onClose = jest.fn();

    await render(
      <ModelEffortModal
        visible={true}
        onClose={onClose}
        engines={mockEngines}
        currentProvider="claude"
        currentModel="sonnet"
        currentEffort="low"
        isDraft={true}
        onSelect={onSelect}
      />,
    );

    // Pick Opus (no reasoning effort)
    await fireEvent.press(screen.getByTestId('model-option-opus'));
    expect(await screen.findByText('Reasoning effort is not configurable for this model.')).toBeTruthy();

    // Pick Sonnet again
    await fireEvent.press(screen.getByTestId('model-option-sonnet'));
    expect(await screen.findByTestId('effort-chip-high')).toBeTruthy();

    // Pick high effort
    await fireEvent.press(screen.getByTestId('effort-chip-high'));

    // Apply
    await fireEvent.press(screen.getByTestId('apply-model-effort-btn'));
    expect(onSelect).toHaveBeenCalledWith({
      provider: 'claude',
      model: 'sonnet',
      effort: 'high',
    });
    expect(onClose).toHaveBeenCalled();
  });

  it('switches engine in draft mode and updates available models', async () => {
    const onSelect = jest.fn();
    const onClose = jest.fn();

    await render(
      <ModelEffortModal
        visible={true}
        onClose={onClose}
        engines={mockEngines}
        currentProvider="claude"
        currentModel="sonnet"
        currentEffort={null}
        isDraft={true}
        onSelect={onSelect}
      />,
    );

    expect(await screen.findByTestId('engine-chip-antigravity')).toBeTruthy();
    await fireEvent.press(screen.getByTestId('engine-chip-antigravity'));

    expect(await screen.findByText('Gemini 2.5 Flash')).toBeTruthy();
    expect(await screen.findByText('Gemini 2.5 Pro')).toBeTruthy();
  });
});

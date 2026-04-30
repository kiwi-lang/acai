import { useState } from 'react';
import { createPortal } from 'react-dom';
import {
    Box, VStack, HStack, Text, Heading, IconButton, Input,
    Spinner, Textarea, Button,
} from '@chakra-ui/react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import {
    createSkill,
    createWorkflowSkill,
} from '../services/api';

const CloseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
    </svg>
);

const DEFAULT_CODE = `#!/usr/bin/env python3
import json, sys


def main():
    args = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    result = {"status": "ok"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()
`;

export interface SkillFormData {
    namespace: string;
    name: string;
    description: string;
    code: string;
    parameters: string;
}

export const emptySkillForm: SkillFormData = {
    namespace: '',
    name: '',
    description: '',
    code: DEFAULT_CODE,
    parameters: JSON.stringify({ type: 'object', properties: {}, required: [] }, null, 2),
};

export interface SkillEditModalProps {
    onSave: () => void;
    onClose: () => void;
    /** When set, creates/saves the skill inside this workflow directory. */
    workflowId?: string;
    /** When set, the modal is in "edit" mode for an existing skill. */
    editingName?: string | null;
    /** Pre-populated form data for edit mode. */
    initialForm?: SkillFormData;
}

const SkillEditModal = ({ onSave, onClose, workflowId, editingName, initialForm }: SkillEditModalProps) => {
    const [form, setForm] = useState<SkillFormData>(initialForm ? { ...initialForm } : { ...emptySkillForm });
    const [formError, setFormError] = useState('');
    const [busy, setBusy] = useState(false);
    const [activeTab, setActiveTab] = useState<'config' | 'code' | 'parameters'>('config');

    const setField = <K extends keyof SkillFormData>(key: K, value: SkillFormData[K]) =>
        setForm(prev => ({ ...prev, [key]: value }));

    const handleSubmit = async () => {
        if (!form.namespace.trim()) { setFormError('Namespace is required'); return; }
        if (!form.name.trim()) { setFormError('Name is required'); return; }
        setBusy(true);
        setFormError('');
        try {
            if (workflowId) {
                await createWorkflowSkill(workflowId, {
                    namespace: form.namespace.trim(),
                    name: form.name.trim(),
                    description: form.description.trim(),
                    code: form.code,
                    parameters: form.parameters,
                });
            } else {
                await createSkill({
                    namespace: form.namespace.trim(),
                    name: form.name.trim(),
                    description: form.description.trim() || `${form.namespace.trim()}.${form.name.trim()} skill`,
                });
            }
            onSave();
        } catch (err) {
            setFormError(err instanceof Error ? err.message : 'Failed');
        } finally {
            setBusy(false);
        }
    };

    const handleBackdrop = (e: React.MouseEvent) => {
        if (e.target === e.currentTarget) onClose();
    };

    return createPortal(
        <Box
            position="fixed" inset={0} zIndex={1400}
            display="flex" alignItems="center" justifyContent="center"
            onClick={handleBackdrop}
        >
            <Box position="absolute" inset={0} bg="blackAlpha.600" />
            <Box
                position="relative" zIndex={1}
                bg="var(--bg-page)" borderRadius="xl"
                border="1px solid" borderColor="var(--border-primary)"
                boxShadow="xl" w="full" maxW="680px" mx={4}
                maxH="90vh" h="85vh" display="flex" flexDirection="column"
            >
                {/* Header */}
                <HStack px={5} py={4} borderBottom="1px solid" borderColor="var(--border-primary)" justify="space-between" flexShrink={0}>
                    <Heading size="sm" color="var(--text-heading)">
                        {editingName ? `Edit — ${editingName}` : workflowId ? 'New Workflow Skill' : 'New Skill'}
                    </Heading>
                    <IconButton aria-label="Close" variant="ghost" size="sm" color="var(--text-tertiary)"
                        _hover={{ color: 'var(--text-heading)' }} onClick={onClose}>
                        <CloseIcon />
                    </IconButton>
                </HStack>

                {/* Tabs */}
                <HStack px={5} pt={3} gap={0} borderBottom="1px solid" borderColor="var(--border-primary)" flexShrink={0}>
                    {(['config', 'code', 'parameters'] as const).map(tab => (
                        <Button
                            key={tab}
                            size="sm"
                            variant="ghost"
                            borderBottom="2px solid"
                            borderColor={activeTab === tab ? 'var(--accent, teal.400)' : 'transparent'}
                            borderRadius={0}
                            color={activeTab === tab ? 'var(--text-heading)' : 'var(--text-muted)'}
                            fontWeight={activeTab === tab ? 'medium' : 'normal'}
                            onClick={() => setActiveTab(tab)}
                            px={4} mb="-1px"
                            _hover={{ color: 'var(--text-heading)' }}
                        >
                            {tab === 'config' ? 'Configuration' : tab === 'code' ? 'run.py' : 'tool.json'}
                        </Button>
                    ))}
                </HStack>

                {/* Body */}
                <Box flex={1} overflowY="auto" px={5} py={4} display="flex" flexDirection="column" minH={0}>
                    {formError && (
                        <Box p={2} bg="var(--bg-error)" borderRadius="md" mb={3} flexShrink={0}>
                            <Text color="var(--text-error)" fontSize="xs">{formError}</Text>
                        </Box>
                    )}

                    {activeTab === 'config' ? (
                        <VStack gap={3} align="stretch">
                            <HStack gap={3}>
                                <Box flex={1}>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Namespace</Text>
                                    <Input
                                        size="sm" placeholder="e.g. data"
                                        value={form.namespace}
                                        onChange={e => setField('namespace', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                        readOnly={!!editingName}
                                        opacity={editingName ? 0.6 : 1}
                                    />
                                </Box>
                                <Box flex={1}>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Name</Text>
                                    <Input
                                        size="sm" placeholder="e.g. summarize"
                                        value={form.name}
                                        onChange={e => setField('name', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                        readOnly={!!editingName}
                                        opacity={editingName ? 0.6 : 1}
                                    />
                                </Box>
                            </HStack>
                            <Box>
                                <Text fontSize="xs" color="var(--text-muted)" mb={1}>Description</Text>
                                <Textarea
                                    size="sm" placeholder="What does this skill do?"
                                    value={form.description}
                                    onChange={e => setField('description', e.target.value)}
                                    bg="var(--bg-input)" color="var(--text-primary)"
                                    borderColor="var(--border-input)"
                                    rows={3}
                                />
                            </Box>
                            {form.namespace && form.name && (
                                <Box>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Qualified Name</Text>
                                    <Text fontSize="sm" fontFamily="mono" color="var(--text-primary)">
                                        {form.namespace.trim()}.{form.name.trim()}
                                    </Text>
                                </Box>
                            )}
                        </VStack>
                    ) : activeTab === 'code' ? (
                        <Box position="relative" flex={1} minH={0}>
                            <Textarea
                                position="absolute"
                                inset={0}
                                fontFamily="mono"
                                fontSize="xs"
                                lineHeight="1.6"
                                value={form.code}
                                onChange={e => setField('code', e.target.value)}
                                bg="transparent"
                                color="transparent"
                                caretColor="var(--text-primary)"
                                borderColor="var(--border-input)"
                                borderRadius="md"
                                resize="none"
                                zIndex={2}
                                p="16px"
                                spellCheck={false}
                                h="100%"
                                _focus={{ outline: 'none', boxShadow: 'none', borderColor: 'var(--accent, teal.400)' }}
                                placeholder="Python code for run.py..."
                            />
                            <Box
                                position="absolute"
                                inset={0}
                                borderRadius="md"
                                overflow="auto"
                                pointerEvents="none"
                                zIndex={1}
                            >
                                <SyntaxHighlighter
                                    language="python"
                                    style={oneDark}
                                    customStyle={{
                                        margin: 0,
                                        padding: '16px',
                                        fontSize: '0.75rem',
                                        lineHeight: '1.6',
                                        background: 'var(--bg-input, #1e1e1e)',
                                        minHeight: '100%',
                                        borderRadius: '0.375rem',
                                    }}
                                    codeTagProps={{ style: { fontFamily: 'var(--fonts-mono, monospace)' } }}
                                >
                                    {form.code || ' '}
                                </SyntaxHighlighter>
                            </Box>
                        </Box>
                    ) : (
                        <Box position="relative" flex={1} minH={0}>
                            <Textarea
                                position="absolute"
                                inset={0}
                                fontFamily="mono"
                                fontSize="xs"
                                lineHeight="1.6"
                                value={form.parameters}
                                onChange={e => setField('parameters', e.target.value)}
                                bg="transparent"
                                color="transparent"
                                caretColor="var(--text-primary)"
                                borderColor="var(--border-input)"
                                borderRadius="md"
                                resize="none"
                                zIndex={2}
                                p="16px"
                                spellCheck={false}
                                h="100%"
                                _focus={{ outline: 'none', boxShadow: 'none', borderColor: 'var(--accent, teal.400)' }}
                                placeholder="JSON schema for parameters..."
                            />
                            <Box
                                position="absolute"
                                inset={0}
                                borderRadius="md"
                                overflow="auto"
                                pointerEvents="none"
                                zIndex={1}
                            >
                                <SyntaxHighlighter
                                    language="json"
                                    style={oneDark}
                                    customStyle={{
                                        margin: 0,
                                        padding: '16px',
                                        fontSize: '0.75rem',
                                        lineHeight: '1.6',
                                        background: 'var(--bg-input, #1e1e1e)',
                                        minHeight: '100%',
                                        borderRadius: '0.375rem',
                                    }}
                                    codeTagProps={{ style: { fontFamily: 'var(--fonts-mono, monospace)' } }}
                                >
                                    {form.parameters || ' '}
                                </SyntaxHighlighter>
                            </Box>
                        </Box>
                    )}
                </Box>

                {/* Footer */}
                <HStack px={5} py={3} borderTop="1px solid" borderColor="var(--border-primary)" justify="flex-end" gap={2} flexShrink={0}>
                    <Button size="sm" variant="ghost" color="var(--text-tertiary)" onClick={onClose}>
                        Cancel
                    </Button>
                    <Button size="sm" colorScheme="green" onClick={handleSubmit} disabled={busy}>
                        {busy ? <Spinner size="xs" /> : editingName ? 'Save' : 'Create'}
                    </Button>
                </HStack>
            </Box>
        </Box>,
        document.body,
    );
};

export default SkillEditModal;

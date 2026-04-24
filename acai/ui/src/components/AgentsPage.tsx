import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useParams, useNavigate } from 'react-router-dom';
import {
    Box, VStack, HStack, Text, Heading, Badge, IconButton, Input,
    NativeSelect, Spinner, Textarea, Button,
} from '@chakra-ui/react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import {
    listAgents, createAgent, updateAgent, deleteAgent, resetAgent,
    getAgentTemplate, updateAgentTemplate, listProviders,
    listToolNamespaces, listSkills,
} from '../services/api';
import type { ToolNamespace, SkillSummary } from '../services/api';
import type { AgentDef, Provider } from '../services/types';

const ROLES = ['worker', 'curator', 'manager'];

const DEFAULT_TEMPLATE = `{%- set system_prompt -%}
You are {{ agent.name }}{% if agent.description %}, {{ agent.description }}{% endif %}.
{% if task.project_obj %}

You are working on project **{{ task.project_obj.name }}** ({{ task.project_obj.language }}).
{% if task.project_spec %}

## Project Specification
{{ task.project_spec }}
{% endif %}
{% endif %}
{% if tools_description %}

## Available Tools
{{ tools_description }}
{% endif %}

Answer questions, suggest plans, and create tasks when asked.
{%- endset -%}
[
  {"role": "system", "content": {{ system_prompt | tojson }}}
{% for msg in messages %},
  {"role": {{ msg.role | tojson }}, "content": {{ msg.content | tojson }}}
{% endfor %}
]
`;

const PlusIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
);

const TrashIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14H6L5 6" />
        <path d="M10 11v6" /><path d="M14 11v6" /><path d="M9 6V4h6v2" />
    </svg>
);

const EditIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
);

const ResetIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="1 4 1 10 7 10" />
        <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
    </svg>
);

const CloseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
    </svg>
);

const OUTPUT_FORMATS = ['messages', 'text'] as const;

interface AgentFormData {
    name: string;
    description: string;
    role: string;
    avatar: string;
    provider: string;
    output_format: string;
    temperature: string;
    max_tokens: string;
    tools: string;
    tool_permissions_read: boolean;
    tool_permissions_write: boolean;
    tool_permissions_execute: boolean;
    context_sources: string;
    max_iterations: string;
    approval_required: boolean;
    tags: string;
    uses_sandbox: boolean;
}

const emptyForm: AgentFormData = {
    name: '', description: '', role: 'worker', avatar: '',
    provider: 'auto', output_format: 'messages',
    temperature: '0.7', max_tokens: '4096',
    tools: '', tool_permissions_read: true, tool_permissions_write: false, tool_permissions_execute: false,
    context_sources: '', max_iterations: '20',
    approval_required: false, tags: '',
    uses_sandbox: true,
};

const ROLE_COLORS: Record<string, string> = {
    worker: 'blue',
    curator: 'purple',
    manager: 'orange',
};

const formToPayload = (f: AgentFormData) => {
    const model_overrides: Record<string, any> = {};
    const temp = parseFloat(f.temperature);
    if (!isNaN(temp)) model_overrides.temperature = temp;
    const mt = parseInt(f.max_tokens);
    if (!isNaN(mt)) model_overrides.max_tokens = mt;

    return {
        name: f.name.trim(),
        description: f.description,
        role: f.role,
        avatar: f.avatar,
        provider: f.provider,
        output_format: f.output_format,
        model_overrides,
        tools: f.tools ? f.tools.split(',').map(s => s.trim()).filter(Boolean) : [],
        tool_permissions: [
            ...(f.tool_permissions_read ? ['read'] : []),
            ...(f.tool_permissions_write ? ['write'] : []),
            ...(f.tool_permissions_execute ? ['execute'] : []),
        ],
        context_sources: f.context_sources ? f.context_sources.split(',').map(s => s.trim()).filter(Boolean) : [],
        max_iterations: parseInt(f.max_iterations) || 20,
        approval_required: f.approval_required,
        tags: f.tags ? f.tags.split(',').map(s => s.trim()).filter(Boolean) : [],
        uses_sandbox: f.uses_sandbox,
    };
};

/* ─── Tool & Skill Namespace Picker ────────────────────────────── */

interface ToolNamespacePickerProps {
    namespaces: ToolNamespace[];
    skills: SkillSummary[];
    value: string;
    onChange: (v: string) => void;
}

const ToolNamespacePicker = ({ namespaces, skills, value, onChange }: ToolNamespacePickerProps) => {
    const selected = useMemo(() => value.split(',').map(s => s.trim()).filter(Boolean), [value]);

    const toggle = (ns: string) => {
        const next = selected.includes(ns)
            ? selected.filter(n => n !== ns)
            : [...selected, ns];
        onChange(next.join(', '));
    };

    const builtinNs = useMemo(
        () => namespaces.filter(ns => !ns.namespace.startsWith('skills.')),
        [namespaces],
    );

    const skillGroups = useMemo(() => {
        const groups: Record<string, { namespace: string; toolCount: number; skillNames: string[] }> = {};
        for (const sk of skills) {
            const key = `skills.${sk.namespace}`;
            if (!groups[key]) {
                const match = namespaces.find(ns => ns.namespace === key);
                groups[key] = { namespace: key, toolCount: match?.tools.length ?? 0, skillNames: [] };
            }
            groups[key].skillNames.push(sk.name);
        }
        return Object.values(groups).sort((a, b) => a.namespace.localeCompare(b.namespace));
    }, [skills, namespaces]);

    return (
        <VStack gap={2} align="stretch">
            <HStack gap={2} flexWrap="wrap">
                {builtinNs.map(ns => {
                    const isActive = selected.includes(ns.namespace);
                    return (
                        <Box
                            key={ns.namespace}
                            as="button"
                            px={3} py={1}
                            borderRadius="md"
                            fontSize="xs"
                            fontWeight="medium"
                            border="1px solid"
                            borderColor={isActive ? 'var(--accent)' : 'var(--border-primary)'}
                            bg={isActive ? 'var(--accent-subtle)' : 'transparent'}
                            color={isActive ? 'var(--accent)' : 'var(--text-tertiary)'}
                            cursor="pointer"
                            _hover={{ borderColor: 'var(--accent)' }}
                            title={ns.tools.join(', ')}
                            onClick={() => toggle(ns.namespace)}
                        >
                            {ns.namespace}
                            <Text as="span" fontSize="2xs" color="var(--text-muted)" ml={1}>
                                ({ns.tools.length})
                            </Text>
                        </Box>
                    );
                })}
            </HStack>

            {skillGroups.length > 0 && (
                <>
                    <Text fontSize="xs" color="var(--text-muted)">
                        Skills
                    </Text>
                    <HStack gap={2} flexWrap="wrap">
                        {skillGroups.map(sg => {
                            const isActive = selected.includes(sg.namespace);
                            const label = sg.namespace.replace(/^skills\./, '');
                            return (
                                <Box
                                    key={sg.namespace}
                                    as="button"
                                    px={3} py={1}
                                    borderRadius="md"
                                    fontSize="xs"
                                    fontWeight="medium"
                                    border="1px solid"
                                    borderColor={isActive ? 'yellow.400' : 'var(--border-primary)'}
                                    bg={isActive ? 'yellow.900' : 'transparent'}
                                    color={isActive ? 'yellow.200' : 'var(--text-tertiary)'}
                                    cursor="pointer"
                                    _hover={{ borderColor: 'yellow.400' }}
                                    title={sg.skillNames.join(', ')}
                                    onClick={() => toggle(sg.namespace)}
                                >
                                    {label}
                                    <Text as="span" fontSize="2xs" color="var(--text-muted)" ml={1}>
                                        ({sg.skillNames.length})
                                    </Text>
                                </Box>
                            );
                        })}
                    </HStack>
                </>
            )}
        </VStack>
    );
};

/* ─── Agent Edit/Create Modal ──────────────────────────────────── */

interface AgentEditModalProps {
    editingName: string | null;
    initialForm: AgentFormData;
    initialTemplate: string;
    providers: Provider[];
    toolNamespaces: ToolNamespace[];
    skills: SkillSummary[];
    onSave: () => void;
    onClose: () => void;
}

const AgentEditModal = ({
    editingName, initialForm, initialTemplate,
    providers, toolNamespaces, skills, onSave, onClose,
}: AgentEditModalProps) => {
    const [form, setForm] = useState<AgentFormData>(initialForm);
    const [formError, setFormError] = useState('');
    const [busy, setBusy] = useState(false);
    const [templateContent, setTemplateContent] = useState(initialTemplate);
    const [templateDirty, setTemplateDirty] = useState(false);
    const [savingTemplate, setSavingTemplate] = useState(false);
    const [activeTab, setActiveTab] = useState<'config' | 'template'>('config');

    const setField = (key: keyof AgentFormData, value: any) =>
        setForm(prev => ({ ...prev, [key]: value }));

    const handleSubmit = async () => {
        if (!form.name.trim()) { setFormError('Name is required'); return; }
        setBusy(true);
        setFormError('');
        try {
            const payload = formToPayload(form) as Partial<AgentDef>;
            if (editingName) {
                await updateAgent(editingName, payload);
                if (templateDirty) {
                    await updateAgentTemplate(editingName, templateContent);
                }
            } else {
                await createAgent(payload);
                const slug = (payload.name ?? '').replace(/\s+/g, '-').toLowerCase();
                await updateAgentTemplate(slug, templateContent);
            }
            onSave();
        } catch (err) {
            setFormError(err instanceof Error ? err.message : 'Failed');
        } finally {
            setBusy(false);
        }
    };

    const handleSaveTemplate = async () => {
        if (!editingName) return;
        setSavingTemplate(true);
        try {
            await updateAgentTemplate(editingName, templateContent);
            setTemplateDirty(false);
        } catch { /* ignore */ } finally { setSavingTemplate(false); }
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
                        {editingName ? `Edit — ${editingName}` : 'New Agent'}
                    </Heading>
                    <IconButton aria-label="Close" variant="ghost" size="sm" color="var(--text-tertiary)"
                        _hover={{ color: 'var(--text-heading)' }} onClick={onClose}>
                        <CloseIcon />
                    </IconButton>
                </HStack>

                {/* Tabs */}
                <HStack px={5} pt={3} gap={0} borderBottom="1px solid" borderColor="var(--border-primary)" flexShrink={0}>
                    {(['config', 'template'] as const).map(tab => (
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
                            {tab === 'config' ? 'Configuration' : 'System Template'}
                        </Button>
                    ))}
                </HStack>

                {/* Body */}
                <Box flex={1} overflowY="auto" px={5} py={4}>
                    {formError && (
                        <Box p={2} bg="var(--bg-error)" borderRadius="md" mb={3}>
                            <Text color="var(--text-error)" fontSize="xs">{formError}</Text>
                        </Box>
                    )}

                    {activeTab === 'config' ? (
                        <VStack gap={3} align="stretch">
                            {/* Row: Name + Role */}
                            <HStack gap={3}>
                                <Box flex={2}>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Name</Text>
                                    <Input
                                        size="sm" placeholder="e.g. code-reviewer"
                                        value={form.name}
                                        onChange={e => setField('name', e.target.value)}
                                        disabled={!!editingName}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    />
                                </Box>
                                <Box flex={1}>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Role</Text>
                                    <NativeSelect.Root size="sm">
                                        <NativeSelect.Field
                                            value={form.role}
                                            onChange={e => setField('role', e.target.value)}
                                            bg="var(--bg-input)" color="var(--text-primary)"
                                            borderColor="var(--border-input)"
                                        >
                                            {ROLES.map(r => (
                                                <option key={r} value={r} style={{ background: 'var(--option-bg)' }}>{r}</option>
                                            ))}
                                        </NativeSelect.Field>
                                    </NativeSelect.Root>
                                </Box>
                                <Box flex={1}>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Avatar</Text>
                                    <Input
                                        size="sm" placeholder="emoji"
                                        value={form.avatar}
                                        onChange={e => setField('avatar', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    />
                                </Box>
                            </HStack>

                            {/* Description */}
                            <Box>
                                <Text fontSize="xs" color="var(--text-muted)" mb={1}>Description</Text>
                                <Input
                                    size="sm" placeholder="What does this agent do?"
                                    value={form.description}
                                    onChange={e => setField('description', e.target.value)}
                                    bg="var(--bg-input)" color="var(--text-primary)"
                                    borderColor="var(--border-input)"
                                />
                            </Box>

                            {/* Provider */}
                            <Box>
                                <Text fontSize="xs" color="var(--text-muted)" mb={1}>Provider</Text>
                                <NativeSelect.Root size="sm">
                                    <NativeSelect.Field
                                        value={form.provider}
                                        onChange={e => setField('provider', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    >
                                        <option value="auto" style={{ background: 'var(--option-bg)' }}>Auto (highest priority)</option>
                                        {providers.map(p => (
                                            <option key={p.name} value={p.name} style={{ background: 'var(--option-bg)' }}>
                                                {p.name} ({p.model || p.backend})
                                            </option>
                                        ))}
                                    </NativeSelect.Field>
                                </NativeSelect.Root>
                            </Box>

                            {/* Output format */}
                            <Box>
                                <Text fontSize="xs" color="var(--text-muted)" mb={1}>Output Format</Text>
                                <NativeSelect.Root size="sm">
                                    <NativeSelect.Field
                                        value={form.output_format}
                                        onChange={e => setField('output_format', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    >
                                        {OUTPUT_FORMATS.map(fmt => (
                                            <option key={fmt} value={fmt} style={{ background: 'var(--option-bg)' }}>
                                                {fmt === 'messages' ? 'messages (JSON array)' : 'text (system prompt)'}
                                            </option>
                                        ))}
                                    </NativeSelect.Field>
                                </NativeSelect.Root>
                            </Box>

                            {/* Model overrides */}
                            <HStack gap={3}>
                                <Box flex={1}>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Temperature</Text>
                                    <Input
                                        size="sm" type="number" step="0.1" placeholder="0.7"
                                        value={form.temperature}
                                        onChange={e => setField('temperature', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    />
                                </Box>
                                <Box flex={1}>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Max Tokens</Text>
                                    <Input
                                        size="sm" type="number" placeholder="4096"
                                        value={form.max_tokens}
                                        onChange={e => setField('max_tokens', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    />
                                </Box>
                                <Box flex={1}>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>Max Iterations</Text>
                                    <Input
                                        size="sm" type="number" placeholder="20"
                                        value={form.max_iterations}
                                        onChange={e => setField('max_iterations', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    />
                                </Box>
                            </HStack>

                            {/* Tool permissions */}
                            <Box>
                                <Text fontSize="xs" color="var(--text-muted)" mb={1}>Permissions</Text>
                                <HStack gap={3}>
                                    {(['read', 'write', 'execute'] as const).map(perm => {
                                        const key = `tool_permissions_${perm}` as keyof AgentFormData;
                                        const isOn = form[key] as boolean;
                                        const colors = { read: 'blue', write: 'orange', execute: 'red' };
                                        return (
                                            <Box
                                                key={perm}
                                                as="button"
                                                px={3} py={1}
                                                borderRadius="md"
                                                fontSize="xs"
                                                fontWeight="medium"
                                                border="1px solid"
                                                borderColor={isOn ? `${colors[perm]}.400` : 'var(--border-primary)'}
                                                bg={isOn ? `${colors[perm]}.900` : 'transparent'}
                                                color={isOn ? `${colors[perm]}.200` : 'var(--text-tertiary)'}
                                                cursor="pointer"
                                                _hover={{ borderColor: `${colors[perm]}.400` }}
                                                onClick={() => setField(key, !isOn)}
                                            >
                                                {perm}
                                            </Box>
                                        );
                                    })}
                                </HStack>
                            </Box>

                            {/* Tool namespaces */}
                            <Box>
                                <Text fontSize="xs" color="var(--text-muted)" mb={1}>
                                    Tools
                                </Text>
                                {toolNamespaces.length > 0 ? (
                                    <ToolNamespacePicker
                                        namespaces={toolNamespaces}
                                        skills={skills}
                                        value={form.tools}
                                        onChange={v => setField('tools', v)}
                                    />
                                ) : (
                                    <Input
                                        size="sm" placeholder="filesystem, git, shell"
                                        value={form.tools}
                                        onChange={e => setField('tools', e.target.value)}
                                        bg="var(--bg-input)" color="var(--text-primary)"
                                        borderColor="var(--border-input)"
                                    />
                                )}
                            </Box>

                            {/* Context sources */}
                            <Box>
                                <Text fontSize="xs" color="var(--text-muted)" mb={1}>
                                    Context sources (comma-separated file globs)
                                </Text>
                                <Input
                                    size="sm" placeholder="docs/goal.md, docs/overview.md"
                                    value={form.context_sources}
                                    onChange={e => setField('context_sources', e.target.value)}
                                    bg="var(--bg-input)" color="var(--text-primary)"
                                    borderColor="var(--border-input)"
                                />
                            </Box>

                            {/* Tags */}
                            <Box>
                                <Text fontSize="xs" color="var(--text-muted)" mb={1}>
                                    Tags (comma-separated)
                                </Text>
                                <Input
                                    size="sm" placeholder="python, review"
                                    value={form.tags}
                                    onChange={e => setField('tags', e.target.value)}
                                    bg="var(--bg-input)" color="var(--text-primary)"
                                    borderColor="var(--border-input)"
                                />
                            </Box>

                            {/* Toggles */}
                            <HStack gap={3} flexWrap="wrap">
                                <Box
                                    as="button"
                                    px={3} py={1}
                                    borderRadius="md"
                                    fontSize="xs"
                                    fontWeight="medium"
                                    border="1px solid"
                                    borderColor={form.approval_required ? 'var(--accent)' : 'var(--border-primary)'}
                                    bg={form.approval_required ? 'var(--accent-subtle)' : 'transparent'}
                                    color={form.approval_required ? 'var(--accent)' : 'var(--text-tertiary)'}
                                    cursor="pointer"
                                    onClick={() => setField('approval_required', !form.approval_required)}
                                    _hover={{ borderColor: 'var(--accent)' }}
                                >
                                    {form.approval_required ? 'Approval required' : 'No approval needed'}
                                </Box>
                                <Box
                                    as="button"
                                    px={3} py={1}
                                    borderRadius="md"
                                    fontSize="xs"
                                    fontWeight="medium"
                                    border="1px solid"
                                    borderColor={form.uses_sandbox ? 'var(--accent)' : 'var(--border-primary)'}
                                    bg={form.uses_sandbox ? 'var(--accent-subtle)' : 'transparent'}
                                    color={form.uses_sandbox ? 'var(--accent)' : 'var(--text-tertiary)'}
                                    cursor="pointer"
                                    onClick={() => setField('uses_sandbox', !form.uses_sandbox)}
                                    _hover={{ borderColor: 'var(--accent)' }}
                                >
                                    {form.uses_sandbox ? 'Sandbox: Enabled' : 'Sandbox: Disabled'}
                                </Box>
                                <Text fontSize="2xs" color="var(--text-muted)">
                                    Sandbox backend is configured in Settings
                                </Text>
                            </HStack>
                        </VStack>
                    ) : (
                        /* Template tab */
                        <VStack gap={3} align="stretch" h="100%">
                            <HStack justify="space-between" flexShrink={0}>
                                <Text fontSize="xs" color="var(--text-muted)">
                                    Jinja2 system prompt template
                                </Text>
                                {editingName && templateDirty && (
                                    <Button
                                        size="xs"
                                        colorScheme="green"
                                        onClick={handleSaveTemplate}
                                        disabled={savingTemplate}
                                    >
                                        {savingTemplate ? <Spinner size="xs" /> : 'Save template'}
                                    </Button>
                                )}
                            </HStack>
                            <Box position="relative" flex={1} minH={0}>
                                <Textarea
                                    position="absolute"
                                    inset={0}
                                    fontFamily="mono"
                                    fontSize="xs"
                                    lineHeight="1.6"
                                    value={templateContent}
                                    onChange={e => { setTemplateContent(e.target.value); setTemplateDirty(true); }}
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
                                    placeholder="Jinja2 system prompt template..."
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
                                        language="django"
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
                                        {templateContent || ' '}
                                    </SyntaxHighlighter>
                                </Box>
                            </Box>
                        </VStack>
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

/* ─── Main Page ────────────────────────────────────────────────── */

const AgentsPage = () => {
    const { agentName: urlAgent } = useParams<{ agentName?: string }>();
    const navigate = useNavigate();

    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [toolNamespaces, setToolNamespaces] = useState<ToolNamespace[]>([]);
    const [skills, setSkills] = useState<SkillSummary[]>([]);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);

    const [modalOpen, setModalOpen] = useState(false);
    const [editingName, setEditingName] = useState<string | null>(null);
    const [modalForm, setModalForm] = useState<AgentFormData>(emptyForm);
    const [modalTemplate, setModalTemplate] = useState(DEFAULT_TEMPLATE);

    const urlHandled = useRef<string | undefined>(undefined);

    const refresh = useCallback(() => {
        listAgents().then(setAgents).catch(err => setError(err instanceof Error ? err.message : 'Failed to load'));
    }, []);

    useEffect(() => {
        document.title = 'Agents - Açaí';
        refresh();
        listProviders().then(setProviders).catch(() => {});
        listToolNamespaces().then(setToolNamespaces).catch(() => {});
        listSkills().then(setSkills).catch(() => {});
    }, [refresh]);

    useEffect(() => {
        if (!urlAgent || agents.length === 0 || urlHandled.current === urlAgent) return;
        const agent = agents.find(a => a.name === urlAgent);
        if (agent) {
            urlHandled.current = urlAgent;
            openEdit(agent);
        }
    }, [urlAgent, agents]);

    const openAdd = () => {
        setModalForm(emptyForm);
        setEditingName(null);
        setModalTemplate(DEFAULT_TEMPLATE);
        setModalOpen(true);
        navigate('/agents/new', { replace: true });
    };

    const openEdit = async (a: AgentDef) => {
        setModalForm({
            name: a.name,
            description: a.description,
            role: a.role,
            avatar: a.avatar,
            provider: a.provider,
            output_format: a.output_format || 'messages',
            temperature: String(a.model_overrides?.temperature ?? '0.7'),
            max_tokens: String(a.model_overrides?.max_tokens ?? '4096'),
            tools: a.tools.join(', '),
            tool_permissions_read: (a.tool_permissions ?? ['read']).includes('read'),
            tool_permissions_write: (a.tool_permissions ?? ['read']).includes('write'),
            tool_permissions_execute: (a.tool_permissions ?? ['read']).includes('execute'),
            context_sources: a.context_sources.join(', '),
            max_iterations: String(a.max_iterations),
            approval_required: a.approval_required,
            tags: a.tags.join(', '),
            uses_sandbox: a.uses_sandbox ?? false,
        });
        setEditingName(a.name);

        try {
            const { content } = await getAgentTemplate(a.name);
            setModalTemplate(content);
        } catch {
            setModalTemplate('');
        }

        setModalOpen(true);
        navigate(`/agents/${encodeURIComponent(a.name)}`, { replace: true });
    };

    const closeModal = useCallback(() => {
        setModalOpen(false);
        urlHandled.current = undefined;
        navigate('/agents', { replace: true });
    }, [navigate]);

    const handleDelete = async (name: string) => {
        setBusy(true);
        try {
            await deleteAgent(name);
            refresh();
        } catch { /* ignore */ } finally { setBusy(false); }
    };

    const handleReset = async (name: string) => {
        setBusy(true);
        try {
            await resetAgent(name);
            refresh();
        } catch { /* ignore */ } finally { setBusy(false); }
    };

    const handleModalSave = () => {
        closeModal();
        refresh();
    };

    return (
        <Box h="100vh" w="100%" bg="var(--bg-page)" overflowY="auto" p={6}>
            <Box maxW="4xl" mx="auto">
                <HStack justify="space-between" mb={6}>
                    <Heading size="lg" color="var(--text-heading)">Agents</Heading>
                    <IconButton
                        aria-label="Add agent"
                        size="sm"
                        variant="outline"
                        onClick={openAdd}
                        borderColor="var(--border-primary)"
                        color="var(--text-primary)"
                    >
                        <PlusIcon />
                    </IconButton>
                </HStack>

                {error && (
                    <Box p={3} bg="var(--bg-error)" borderRadius="md" mb={4}>
                        <Text color="var(--text-error)" fontSize="sm">{error}</Text>
                    </Box>
                )}

                {agents.length === 0 && (
                    <Text color="var(--text-muted)" fontSize="sm" py={8} textAlign="center">
                        No agents configured. Click (+) to create one.
                    </Text>
                )}

                <VStack gap={3} align="stretch" mb={6}>
                    {agents.map(a => (
                        <Box
                            key={a.name}
                            p={4}
                            bg="var(--bg-card)"
                            borderRadius="lg"
                            border="1px solid"
                            borderColor="var(--border-primary)"
                            cursor="pointer"
                            _hover={{ borderColor: 'var(--border-secondary)', bg: 'var(--bg-hover)' }}
                            transition="all 0.15s"
                            onClick={() => openEdit(a)}
                        >
                            <HStack justify="space-between" mb={2}>
                                <HStack gap={2}>
                                    {a.avatar && <Text fontSize="lg">{a.avatar}</Text>}
                                    <Text fontWeight="bold" color="var(--text-heading)" fontSize="sm">
                                        {a.name}
                                    </Text>
                                    <Badge colorScheme={ROLE_COLORS[a.role] || 'gray'} fontSize="2xs">
                                        {a.role}
                                    </Badge>
                                    <Badge variant="outline" fontSize="2xs">
                                        {a.provider === 'auto' ? 'auto' : a.provider}
                                    </Badge>
                                    {a.builtin && (
                                        <Badge colorScheme="green" fontSize="2xs" variant="subtle">
                                            built-in
                                        </Badge>
                                    )}
                                </HStack>
                                <HStack gap={1} onClick={e => e.stopPropagation()}>
                                    <IconButton
                                        aria-label="Edit" size="xs" variant="ghost"
                                        onClick={() => openEdit(a)}
                                        color="var(--text-tertiary)"
                                    >
                                        <EditIcon />
                                    </IconButton>
                                    {a.builtin && (
                                        <IconButton
                                            aria-label="Reset" size="xs" variant="ghost"
                                            onClick={() => handleReset(a.name)}
                                            color="var(--text-tertiary)" disabled={busy}
                                        >
                                            <ResetIcon />
                                        </IconButton>
                                    )}
                                    {!a.builtin && (
                                        <IconButton
                                            aria-label="Delete" size="xs" variant="ghost"
                                            onClick={() => handleDelete(a.name)}
                                            color="var(--text-error)" disabled={busy}
                                        >
                                            <TrashIcon />
                                        </IconButton>
                                    )}
                                </HStack>
                            </HStack>

                            {a.description && (
                                <Text fontSize="sm" color="var(--text-secondary)" mb={2}>
                                    {a.description}
                                </Text>
                            )}

                            <HStack gap={6} flexWrap="wrap">
                                <VStack align="flex-start" gap={0}>
                                    <Text fontSize="xs" color="var(--text-muted)">Max Iterations</Text>
                                    <Text fontSize="sm" color="var(--text-primary)">{a.max_iterations}</Text>
                                </VStack>
                                <VStack align="flex-start" gap={0}>
                                    <Text fontSize="xs" color="var(--text-muted)">Format</Text>
                                    <Text fontSize="sm" color="var(--text-primary)">
                                        {a.output_format || 'messages'}
                                    </Text>
                                </VStack>
                                <VStack align="flex-start" gap={0}>
                                    <Text fontSize="xs" color="var(--text-muted)">Approval</Text>
                                    <Text fontSize="sm" color="var(--text-primary)">
                                        {a.approval_required ? 'Required' : 'No'}
                                    </Text>
                                </VStack>
                                {a.uses_sandbox && (
                                    <VStack align="flex-start" gap={0}>
                                        <Text fontSize="xs" color="var(--text-muted)">Sandbox</Text>
                                        <Text fontSize="sm" color="var(--text-primary)">Enabled</Text>
                                    </VStack>
                                )}
                            </HStack>

                            {a.tools.length > 0 && (
                                <HStack mt={2} gap={1} flexWrap="wrap">
                                    <Text fontSize="xs" color="var(--text-muted)">Tools:</Text>
                                    {a.tools.map(t => (
                                        <Badge key={t} fontSize="2xs" variant="outline">{t}</Badge>
                                    ))}
                                </HStack>
                            )}

                            {a.tags.length > 0 && (
                                <HStack mt={1} gap={1} flexWrap="wrap">
                                    <Text fontSize="xs" color="var(--text-muted)">Tags:</Text>
                                    {a.tags.map(t => (
                                        <Badge key={t} fontSize="2xs" colorScheme="teal">{t}</Badge>
                                    ))}
                                </HStack>
                            )}
                        </Box>
                    ))}
                </VStack>

                {/* Edit/Create Modal */}
                {modalOpen && (
                    <AgentEditModal
                        editingName={editingName}
                        initialForm={modalForm}
                        initialTemplate={modalTemplate}
                        providers={providers}
                        toolNamespaces={toolNamespaces}
                        skills={skills}
                        onSave={handleModalSave}
                        onClose={closeModal}
                    />
                )}
            </Box>
        </Box>
    );
};

export default AgentsPage;

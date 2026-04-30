import { useState, useMemo } from 'react';
import { createPortal } from 'react-dom';
import {
    Box, VStack, HStack, Text, Heading, IconButton, Input,
    NativeSelect, Spinner, Textarea, Button,
} from '@chakra-ui/react';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import {
    createAgent, updateAgent, updateAgentTemplate,
    createWorkflowAgent,
    type ToolNamespace, type SkillSummary,
} from '../services/api';
import type { AgentDef, Provider } from '../services/types';

const ROLES = ['worker', 'curator', 'manager'];
const OUTPUT_FORMATS = ['messages', 'text'] as const;

const CloseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
    </svg>
);

export const DEFAULT_TEMPLATE = `{%- set system_prompt -%}
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

export interface AgentFormData {
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
    resource_permissions: string[];
    context_sources: string;
    max_iterations: string;
    approval_required: boolean;
    tags: string;
    scope: string;
    uses_sandbox: boolean;
}

export const emptyForm: AgentFormData = {
    name: '', description: '', role: 'worker', avatar: '',
    provider: 'auto', output_format: 'messages',
    temperature: '0.7', max_tokens: '4096',
    tools: '', tool_permissions_read: true, tool_permissions_write: false, tool_permissions_execute: false,
    resource_permissions: [],
    context_sources: '', max_iterations: '20',
    approval_required: false, tags: '',
    scope: 'global',
    uses_sandbox: true,
};

export const agentDefToForm = (a: AgentDef): AgentFormData => ({
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
    resource_permissions: a.resource_permissions ?? [],
    context_sources: a.context_sources.join(', '),
    max_iterations: String(a.max_iterations),
    approval_required: a.approval_required,
    tags: a.tags.join(', '),
    scope: a.scope || 'global',
    uses_sandbox: a.uses_sandbox ?? false,
});

export const formToPayload = (f: AgentFormData) => {
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
        resource_permissions: f.resource_permissions,
        context_sources: f.context_sources ? f.context_sources.split(',').map(s => s.trim()).filter(Boolean) : [],
        max_iterations: parseInt(f.max_iterations) || 20,
        approval_required: f.approval_required,
        tags: f.tags ? f.tags.split(',').map(s => s.trim()).filter(Boolean) : [],
        scope: f.scope,
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

/* ─── Resource helpers ─────────────────────────────────────────── */

/**
 * Given the previous and next tools string, return an updated
 * resource_permissions array that adds all resource permissions
 * from any newly-enabled namespace (permissions from previously
 * enabled namespaces are left untouched).
 */
export function mergeResourcePermissionsOnToolChange(
    prevTools: string,
    nextTools: string,
    currentResources: string[],
    allNamespaces: ToolNamespace[],
): string[] {
    const prev = new Set(prevTools.split(',').map(s => s.trim()).filter(Boolean));
    const next = nextTools.split(',').map(s => s.trim()).filter(Boolean);
    const added = next.filter(ns => !prev.has(ns));
    if (added.length === 0) return currentResources;

    const toAdd = new Set<string>();
    for (const ns of allNamespaces) {
        if (added.some(en => ns.namespace === en || ns.namespace.startsWith(en + '.'))) {
            for (const rp of ns.resource_permissions ?? []) {
                toAdd.add(rp);
            }
        }
    }
    if (toAdd.size === 0) return currentResources;
    return [...new Set([...currentResources, ...toAdd])];
}

/* ─── Resource Permission Picker ───────────────────────────────── */

export interface ResourcePermissionPickerProps {
    namespaces: ToolNamespace[];
    enabledNamespaces: string[];
    value: string[];
    onChange: (v: string[]) => void;
}

export const ResourcePermissionPicker = ({ namespaces, enabledNamespaces, value, onChange }: ResourcePermissionPickerProps) => {
    const available = useMemo(() => {
        const perms = new Set<string>();
        for (const ns of namespaces) {
            if (enabledNamespaces.some(en => ns.namespace === en || ns.namespace.startsWith(en + '.'))) {
                for (const rp of ns.resource_permissions ?? []) {
                    perms.add(rp);
                }
            }
        }
        return Array.from(perms).sort();
    }, [namespaces, enabledNamespaces]);

    const grouped = useMemo(() => {
        const groups: Record<string, string[]> = {};
        for (const perm of available) {
            const [resource] = perm.split(':');
            (groups[resource] ||= []).push(perm);
        }
        return Object.entries(groups).sort(([a], [b]) => a.localeCompare(b));
    }, [available]);

    const labelWidth = useMemo(() => {
        const longest = grouped.reduce((max, [g]) => Math.max(max, g.length), 0);
        return `${Math.max(longest * 0.65 + 1.2, 5)}rem`;
    }, [grouped]);

    if (available.length === 0) return null;

    const toggle = (perm: string) => {
        const next = value.includes(perm)
            ? value.filter(p => p !== perm)
            : [...value, perm];
        onChange(next);
    };

    const toggleGroup = (group: string, perms: string[]) => {
        const allOn = perms.every(p => value.includes(p));
        const next = allOn
            ? value.filter(p => !perms.includes(p))
            : [...new Set([...value, ...perms])];
        onChange(next);
    };

    return (
        <VStack gap={2} align="stretch">
            {grouped.map(([group, perms]) => (
                <HStack key={group} gap={2} flexWrap="nowrap" align="center">
                    <Box
                        as="button"
                        w={labelWidth}
                        minW={labelWidth}
                        px={3} py={1}
                        borderRadius="md"
                        fontSize="xs"
                        fontWeight="bold"
                        color="var(--text-muted)"
                        cursor="pointer"
                        textAlign="left"
                        flexShrink={0}
                        _hover={{ color: 'var(--text-heading)' }}
                        onClick={() => toggleGroup(group, perms)}
                        title={`Toggle all ${group} permissions`}
                    >
                        {group}
                    </Box>
                    {perms.map(perm => {
                        const verb = perm.split(':')[1];
                        const isOn = value.includes(perm);
                        const verbColor = verb === 'read' ? 'blue' : verb === 'create' || verb === 'update' ? 'orange' : verb === 'delete' ? 'red' : 'purple';
                        return (
                            <Box
                                key={perm}
                                as="button"
                                px={3} py={1}
                                borderRadius="md"
                                fontSize="xs"
                                fontWeight="medium"
                                border="1px solid"
                                borderColor={isOn ? `${verbColor}.400` : 'var(--border-primary)'}
                                bg={isOn ? `${verbColor}.900` : 'transparent'}
                                color={isOn ? `${verbColor}.200` : 'var(--text-tertiary)'}
                                cursor="pointer"
                                _hover={{ borderColor: `${verbColor}.400` }}
                                onClick={() => toggle(perm)}
                                title={perm}
                            >
                                {verb}
                            </Box>
                        );
                    })}
                </HStack>
            ))}
        </VStack>
    );
};

/* ─── Shared Agent Form Body (tabs + config + template) ────────── */

export interface AgentFormBodyProps {
    form: AgentFormData;
    setForm: (fn: (prev: AgentFormData) => AgentFormData) => void;
    templateContent: string;
    setTemplateContent: (v: string) => void;
    templateDirty: boolean;
    setTemplateDirty: (v: boolean) => void;
    providers: Provider[];
    toolNamespaces: ToolNamespace[];
    skills: SkillSummary[];
    editingName: string | null;
    formError?: string;
    /** When provided, a "Save template" button appears while dirty. */
    onSaveTemplate?: () => Promise<void>;
    templateMinH?: string;
}

export const AgentFormBody = ({
    form, setForm,
    templateContent, setTemplateContent,
    templateDirty, setTemplateDirty,
    providers, toolNamespaces, skills,
    editingName, formError,
    onSaveTemplate, templateMinH = '400px',
}: AgentFormBodyProps) => {
    const [activeTab, setActiveTab] = useState<'config' | 'template'>('config');
    const [savingTemplate, setSavingTemplate] = useState(false);

    const setField = (key: keyof AgentFormData, value: any) =>
        setForm(prev => ({ ...prev, [key]: value }));

    const handleSaveTemplate = async () => {
        if (!onSaveTemplate) return;
        setSavingTemplate(true);
        try {
            await onSaveTemplate();
            setTemplateDirty(false);
        } catch { /* ignore */ } finally { setSavingTemplate(false); }
    };

    return (
        <>
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
                        {/* Row: Name + Role + Avatar */}
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
                                as="button" px={3} py={1} borderRadius="md" fontSize="xs" fontWeight="medium"
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
                                as="button" px={3} py={1} borderRadius="md" fontSize="xs" fontWeight="medium"
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
                            <Box
                                as="button" px={3} py={1} borderRadius="md" fontSize="xs" fontWeight="medium"
                                border="1px solid"
                                borderColor={form.scope === 'project' ? 'var(--accent)' : 'var(--border-primary)'}
                                bg={form.scope === 'project' ? 'var(--accent-subtle)' : 'transparent'}
                                color={form.scope === 'project' ? 'var(--accent)' : 'var(--text-tertiary)'}
                                cursor="pointer"
                                onClick={() => setField('scope', form.scope === 'project' ? 'global' : 'project')}
                                _hover={{ borderColor: 'var(--accent)' }}
                            >
                                {form.scope === 'project' ? 'Scope: Project' : 'Scope: Global'}
                            </Box>
                        </HStack>

                        {/* Tool namespaces */}
                        <Box>
                            <Text fontSize="xs" color="var(--text-muted)" mb={1}>Tools</Text>
                            {toolNamespaces.length > 0 ? (
                                <ToolNamespacePicker
                                    namespaces={toolNamespaces}
                                    skills={skills}
                                    value={form.tools}
                                    onChange={v => {
                                        const updated = mergeResourcePermissionsOnToolChange(form.tools, v, form.resource_permissions, toolNamespaces);
                                        setForm(prev => ({ ...prev, tools: v, resource_permissions: updated }));
                                    }}
                                />
                            ) : (
                                <Input
                                    size="sm" placeholder="filesystem, git, shell"
                                    value={form.tools}
                                    onChange={e => {
                                        const v = e.target.value;
                                        const updated = mergeResourcePermissionsOnToolChange(form.tools, v, form.resource_permissions, toolNamespaces);
                                        setForm(prev => ({ ...prev, tools: v, resource_permissions: updated }));
                                    }}
                                    bg="var(--bg-input)" color="var(--text-primary)"
                                    borderColor="var(--border-input)"
                                />
                            )}
                        </Box>

                        {/* Resource permissions (dynamic based on enabled namespaces) */}
                        {(() => {
                            const enabledNs = form.tools.split(',').map(s => s.trim()).filter(Boolean);
                            return (
                                <Box>
                                    <Text fontSize="xs" color="var(--text-muted)" mb={1}>
                                        Resource Permissions
                                    </Text>
                                    <ResourcePermissionPicker
                                        namespaces={toolNamespaces}
                                        enabledNamespaces={enabledNs}
                                        value={form.resource_permissions}
                                        onChange={v => setField('resource_permissions', v)}
                                    />
                                    {enabledNs.length === 0 && (
                                        <Text fontSize="2xs" color="var(--text-muted)">
                                            Enable tool namespaces above to see available resource permissions.
                                        </Text>
                                    )}
                                </Box>
                            );
                        })()}

                        {/* Global permissions */}
                        <Box>
                            <Text fontSize="xs" color="var(--text-muted)" mb={1}>Global Permissions</Text>
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
                    </VStack>
                ) : (
                    /* Template tab */
                    <VStack gap={3} align="stretch" h="100%">
                        <HStack justify="space-between" flexShrink={0}>
                            <Text fontSize="xs" color="var(--text-muted)">
                                Jinja2 system prompt template
                            </Text>
                            {editingName && onSaveTemplate && templateDirty && (
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
                        <Box position="relative" flex={1} minH={templateMinH}>
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
        </>
    );
};

/* ─── Agent Edit/Create Modal ──────────────────────────────────── */

export interface AgentEditModalProps {
    editingName: string | null;
    initialForm: AgentFormData;
    initialTemplate: string;
    providers: Provider[];
    toolNamespaces: ToolNamespace[];
    skills: SkillSummary[];
    onSave: () => void;
    onClose: () => void;
    /** When set, the agent is created/saved inside this workflow directory instead of globally. */
    workflowId?: string;
}

const AgentEditModal = ({
    editingName, initialForm, initialTemplate,
    providers, toolNamespaces, skills, onSave, onClose,
    workflowId,
}: AgentEditModalProps) => {
    const [form, setForm] = useState<AgentFormData>(initialForm);
    const [formError, setFormError] = useState('');
    const [busy, setBusy] = useState(false);
    const [templateContent, setTemplateContent] = useState(initialTemplate);
    const [templateDirty, setTemplateDirty] = useState(false);

    const handleSubmit = async () => {
        if (!form.name.trim()) { setFormError('Name is required'); return; }
        setBusy(true);
        setFormError('');
        try {
            const payload = formToPayload(form) as Partial<AgentDef>;
            if (workflowId) {
                await createWorkflowAgent(workflowId, {
                    ...payload,
                    system_template: templateContent,
                } as any);
            } else if (editingName) {
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

    const handleSaveTemplate = editingName
        ? workflowId
            ? async () => {
                await createWorkflowAgent(workflowId, {
                    ...formToPayload(form),
                    system_template: templateContent,
                } as any);
            }
            : async () => { await updateAgentTemplate(editingName, templateContent); }
        : undefined;

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
                        {editingName ? `Edit — ${editingName}` : workflowId ? 'New Workflow Agent' : 'New Agent'}
                        {workflowId && editingName && <Text as="span" fontSize="xs" color="var(--text-muted)" ml={2}>(workflow)</Text>}
                    </Heading>
                    <IconButton aria-label="Close" variant="ghost" size="sm" color="var(--text-tertiary)"
                        _hover={{ color: 'var(--text-heading)' }} onClick={onClose}>
                        <CloseIcon />
                    </IconButton>
                </HStack>

                <AgentFormBody
                    form={form} setForm={setForm}
                    templateContent={templateContent} setTemplateContent={setTemplateContent}
                    templateDirty={templateDirty} setTemplateDirty={setTemplateDirty}
                    providers={providers} toolNamespaces={toolNamespaces} skills={skills}
                    editingName={editingName}
                    formError={formError}
                    onSaveTemplate={handleSaveTemplate}
                    templateMinH="0"
                />

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

export default AgentEditModal;

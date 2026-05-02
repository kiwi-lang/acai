import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Box, VStack, HStack, Text, Heading, Input, Spinner, IconButton, Badge,
    Button, Textarea,
} from '@chakra-ui/react';
import {
    listSkills, getSkill, createSkill, updateSkillCode, updateSkillReadme,
    updateSkillDefinition, updateSkillRequirements, deleteSkill,
    type SkillSummary, type SkillDetail,
} from '../services/api';
import Markdown from './Markdown';
import ChatPanel from './ChatPanel';
import CodeEditor from './CodeEditor';

const SkillIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" opacity={0.6}>
        <path d="M7 5h10v2h2V3c0-1.1-.9-2-2-2H7c-1.1 0-2 .9-2 2v4h2V5zm8.41 11.59L20 12l-4.59-4.59L14 8.83 17.17 12 14 15.17l1.41 1.42zM10 15.17L6.83 12 10 8.83 8.59 7.41 4 12l4.59 4.59L10 15.17zM17 19H7v-2H5v4c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2v-4h-2v2z" />
    </svg>
);
const FolderIcon = ({ open }: { open: boolean }) => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" opacity={0.6}>
        {open
            ? <path d="M20 6h-8l-2-2H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2zm0 12H4V8h16v10z" />
            : <path d="M10 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z" />
        }
    </svg>
);
const ChevronRight = () => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
        <path d="M10 6L8.59 7.41 13.17 12l-4.58 4.59L10 18l6-6z" />
    </svg>
);
const ChevronDown = () => (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor">
        <path d="M16.59 8.59L12 13.17 7.41 8.59 6 10l6 6 6-6z" />
    </svg>
);
const DeleteIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" />
    </svg>
);
const PlusIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="12" y1="5" x2="12" y2="19" />
        <line x1="5" y1="12" x2="19" y2="12" />
    </svg>
);
const SaveIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <path d="M17 3H5c-1.11 0-2 .9-2 2v14c0 1.1.89 2 2 2h14c1.1 0 2-.9 2-2V7l-4-4zm-5 16c-1.66 0-3-1.34-3-3s1.34-3 3-3 3 1.34 3 3-1.34 3-3 3zm3-10H5V5h10v4z" />
    </svg>
);
const ChatIcon = ({ active }: { active?: boolean }) => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill={active ? 'currentColor' : 'none'}
        stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z" />
    </svg>
);

type Tab = 'code' | 'definition' | 'readme' | 'requirements';

const SkillsPage = () => {
    const [skills, setSkills] = useState<SkillSummary[]>([]);
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [selected, setSelected] = useState<{ namespace: string; name: string } | null>(null);
    const [detail, setDetail] = useState<SkillDetail | null>(null);
    const [detailLoading, setDetailLoading] = useState(false);
    const [activeTab, setActiveTab] = useState<Tab>('code');
    const [search, setSearch] = useState('');

    const [editCode, setEditCode] = useState('');
    const [editReadme, setEditReadme] = useState('');
    const [editRequirements, setEditRequirements] = useState('');
    const [editDescription, setEditDescription] = useState('');
    const [dirty, setDirty] = useState(false);
    const [saving, setSaving] = useState(false);

    const [showCreate, setShowCreate] = useState(false);
    const [newNs, setNewNs] = useState('');
    const [newName, setNewName] = useState('');
    const [newDesc, setNewDesc] = useState('');
    const [creating, setCreating] = useState(false);
    const [showChat, setShowChat] = useState(false);
    const [chatKey, setChatKey] = useState(0);

    const refresh = useCallback(() => {
        setLoading(true);
        listSkills()
            .then(s => { setSkills(s); })
            .catch(() => {})
            .finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        document.title = 'Skills — Açaí';
        refresh();
    }, [refresh]);

    const grouped = useMemo(() => {
        const map: Record<string, SkillSummary[]> = {};
        const q = search.toLowerCase();
        for (const s of skills) {
            if (q && !s.name.toLowerCase().includes(q) && !s.namespace.toLowerCase().includes(q) && !s.description.toLowerCase().includes(q)) continue;
            (map[s.namespace] ||= []).push(s);
        }
        return map;
    }, [skills, search]);

    const toggleExpand = useCallback((key: string) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    }, []);

    const selectSkill = useCallback((namespace: string, name: string) => {
        setSelected({ namespace, name });
        setDetailLoading(true);
        setDirty(false);
        getSkill(namespace, name)
            .then(d => {
                setDetail(d);
                setEditCode(d.code);
                setEditReadme(d.readme);
                setEditRequirements(d.requirements || '');
                setEditDescription(d.definition?.description || '');
                setActiveTab('code');
            })
            .catch(() => setDetail(null))
            .finally(() => setDetailLoading(false));
    }, []);

    const handleSave = useCallback(async () => {
        if (!selected || !detail) return;
        setSaving(true);
        try {
            if (activeTab === 'code' && editCode !== detail.code) {
                await updateSkillCode(selected.namespace, selected.name, editCode);
            }
            if (activeTab === 'readme' && editReadme !== detail.readme) {
                await updateSkillReadme(selected.namespace, selected.name, editReadme);
            }
            if (activeTab === 'definition' && editDescription !== (detail.definition?.description || '')) {
                await updateSkillDefinition(selected.namespace, selected.name, { description: editDescription });
            }
            if (activeTab === 'requirements' && editRequirements !== (detail.requirements || '')) {
                await updateSkillRequirements(selected.namespace, selected.name, editRequirements);
            }
            selectSkill(selected.namespace, selected.name);
            setDirty(false);
        } catch { /* ignore */ }
        finally { setSaving(false); }
    }, [selected, detail, activeTab, editCode, editReadme, editRequirements, editDescription, selectSkill]);

    const handleDelete = useCallback(async () => {
        if (!selected) return;
        try {
            await deleteSkill(selected.namespace, selected.name);
            setSelected(null);
            setDetail(null);
            refresh();
        } catch { /* ignore */ }
    }, [selected, refresh]);

    const handleCreate = useCallback(async () => {
        if (!newNs.trim() || !newName.trim()) return;
        setCreating(true);
        try {
            await createSkill({
                namespace: newNs.trim(),
                name: newName.trim(),
                description: newDesc.trim() || `${newNs.trim()}.${newName.trim()} skill`,
            });
            setShowCreate(false);
            setNewNs('');
            setNewName('');
            setNewDesc('');
            refresh();
        } catch { /* ignore */ }
        finally { setCreating(false); }
    }, [newNs, newName, newDesc, refresh]);

    const chatContext = useMemo(() => {
        if (!selected || !detail) return {};
        return {
            current_skill: JSON.stringify({
                qualified_name: detail.qualified_name,
                namespace: detail.namespace,
                name: detail.name,
                description: detail.definition?.description || '',
                parameters: detail.definition?.parameters || {},
                code_preview: (detail.code || '').slice(0, 2000),
            }, null, 2),
        };
    }, [selected, detail]);

    const handleChatDone = useCallback(() => {
        refresh();
        if (selected) {
            selectSkill(selected.namespace, selected.name);
        }
    }, [refresh, selected, selectSkill]);

    const namespaces = Object.keys(grouped).sort();

    return (
        <Box display="flex" h="100%" bg="var(--bg-page)">
            {/* Sidebar */}
            <Box
                w="300px" minW="300px"
                borderRight="1px solid" borderColor="var(--border-primary)"
                display="flex" flexDirection="column"
                bg="var(--bg-sidebar)"
            >
                <Box p={4} borderBottom="1px solid" borderColor="var(--border-primary)">
                    <HStack justify="space-between" mb={3}>
                        <Heading size="md" color="var(--text-heading)">Skills</Heading>
                        <IconButton
                            aria-label="Create skill"
                            size="sm"
                            variant="ghost"
                            onClick={() => setShowCreate(!showCreate)}
                            color="var(--text-muted)"
                            _hover={{ color: 'green.400' }}
                        >
                            <PlusIcon />
                        </IconButton>
                    </HStack>
                    <Input
                        placeholder="Filter skills…"
                        size="sm"
                        value={search}
                        onChange={e => setSearch(e.target.value)}
                        bg="var(--bg-input)"
                        borderColor="var(--border-secondary)"
                        color="var(--text-primary)"
                        _placeholder={{ color: 'var(--text-muted)' }}
                    />
                </Box>

                {showCreate && (
                    <Box p={4} borderBottom="1px solid" borderColor="var(--border-primary)" bg="var(--bg-active)">
                        <VStack gap={2} align="stretch">
                            <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" textTransform="uppercase">
                                New Skill
                            </Text>
                            <Input
                                placeholder="Namespace"
                                size="sm"
                                value={newNs}
                                onChange={e => setNewNs(e.target.value)}
                                bg="var(--bg-input)"
                                borderColor="var(--border-secondary)"
                                color="var(--text-primary)"
                            />
                            <Input
                                placeholder="Name"
                                size="sm"
                                value={newName}
                                onChange={e => setNewName(e.target.value)}
                                bg="var(--bg-input)"
                                borderColor="var(--border-secondary)"
                                color="var(--text-primary)"
                            />
                            <Input
                                placeholder="Description"
                                size="sm"
                                value={newDesc}
                                onChange={e => setNewDesc(e.target.value)}
                                bg="var(--bg-input)"
                                borderColor="var(--border-secondary)"
                                color="var(--text-primary)"
                            />
                            <Button
                                size="sm"
                                colorScheme="green"
                                onClick={handleCreate}
                                disabled={!newNs.trim() || !newName.trim() || creating}
                            >
                                {creating ? <Spinner size="xs" /> : 'Create'}
                            </Button>
                        </VStack>
                    </Box>
                )}

                <Box flex={1} overflowY="auto" p={3}>
                    {loading ? (
                        <Box textAlign="center" py={8}><Spinner size="lg" color="var(--text-muted)" /></Box>
                    ) : namespaces.length === 0 ? (
                        <Text color="var(--text-muted)" fontSize="sm" textAlign="center" py={8}>
                            No skills found
                        </Text>
                    ) : (
                        <VStack gap={0} align="stretch">
                            {namespaces.map(ns => {
                                const isExpanded = expanded.has(ns);
                                const nsSkills = grouped[ns];
                                return (
                                    <Box key={ns}>
                                        <HStack
                                            p={2} cursor="pointer"
                                            borderRadius="md"
                                            _hover={{ bg: 'var(--bg-hover)' }}
                                            onClick={() => toggleExpand(ns)}
                                            gap={2}
                                        >
                                            <Box color="var(--text-muted)">
                                                {isExpanded ? <ChevronDown /> : <ChevronRight />}
                                            </Box>
                                            <Box color="var(--text-muted)"><FolderIcon open={isExpanded} /></Box>
                                            <Text fontSize="sm" fontWeight="medium" color="var(--text-primary)" flex={1}>
                                                {ns}
                                            </Text>
                                            <Badge
                                                fontSize="2xs"
                                                colorScheme="gray"
                                                variant="subtle"
                                            >
                                                {nsSkills.length}
                                            </Badge>
                                        </HStack>
                                        {isExpanded && nsSkills.map(s => {
                                            const isSelected = selected?.namespace === s.namespace && selected?.name === s.name;
                                            return (
                                                <HStack
                                                    key={s.qualified_name}
                                                    pl={8} pr={2} py={1.5}
                                                    cursor="pointer"
                                                    borderRadius="md"
                                                    bg={isSelected ? 'var(--bg-active)' : 'transparent'}
                                                    _hover={{ bg: 'var(--bg-hover)' }}
                                                    onClick={() => selectSkill(s.namespace, s.name)}
                                                    gap={2}
                                                >
                                                    <Box color={isSelected ? 'yellow.400' : 'var(--text-muted)'}><SkillIcon /></Box>
                                                    <Text
                                                        fontSize="sm"
                                                        color={isSelected ? 'var(--text-heading)' : 'var(--text-secondary)'}
                                                        fontWeight={isSelected ? 'medium' : 'normal'}
                                                        flex={1}
                                                        truncate
                                                    >
                                                        {s.name}
                                                    </Text>
                                                </HStack>
                                            );
                                        })}
                                    </Box>
                                );
                            })}
                        </VStack>
                    )}
                </Box>
            </Box>

            {/* Main content */}
            <Box flex={1} display="flex" flexDirection="column" overflow="hidden">
                {!selected ? (
                    <Box flex={1} display="flex" alignItems="center" justifyContent="center" position="relative">
                        <Box position="absolute" top={4} right={4}>
                            <IconButton
                                aria-label="Toggle AI assistant"
                                size="sm"
                                variant={showChat ? 'solid' : 'ghost'}
                                colorScheme={showChat ? 'green' : undefined}
                                color={showChat ? undefined : 'var(--text-muted)'}
                                _hover={{ color: showChat ? undefined : 'green.400' }}
                                onClick={() => setShowChat(v => !v)}
                            >
                                <ChatIcon active={showChat} />
                            </IconButton>
                        </Box>
                        <VStack gap={3} color="var(--text-muted)">
                            <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" opacity={0.3}>
                                <path d="M7 5h10v2h2V3c0-1.1-.9-2-2-2H7c-1.1 0-2 .9-2 2v4h2V5zm8.41 11.59L20 12l-4.59-4.59L14 8.83 17.17 12 14 15.17l1.41 1.42zM10 15.17L6.83 12 10 8.83 8.59 7.41 4 12l4.59 4.59L10 15.17zM17 19H7v-2H5v4c0 1.1.9 2 2 2h10c1.1 0 2-.9 2-2v-4h-2v2z" />
                            </svg>
                            <Text fontSize="lg">Select a skill to view</Text>
                            <Text fontSize="sm">Or use the AI assistant to create one</Text>
                        </VStack>
                    </Box>
                ) : detailLoading ? (
                    <Box flex={1} display="flex" alignItems="center" justifyContent="center">
                        <Spinner size="lg" color="var(--text-muted)" />
                    </Box>
                ) : detail ? (
                    <>
                        {/* Header */}
                        <Box px={6} py={4} borderBottom="1px solid" borderColor="var(--border-primary)">
                            <HStack justify="space-between">
                                <VStack align="start" gap={1}>
                                    <HStack gap={2}>
                                        <Heading size="md" color="var(--text-heading)">
                                            {detail.name}
                                        </Heading>
                                        <Badge colorScheme="yellow" fontSize="xs">
                                            skills.{detail.namespace}
                                        </Badge>
                                    </HStack>
                                    <Text fontSize="sm" color="var(--text-muted)">
                                        {detail.definition?.description || 'No description'}
                                    </Text>
                                </VStack>
                                <HStack gap={2}>
                                    {dirty && (
                                        <Button
                                            size="sm"
                                            colorScheme="green"
                                            onClick={handleSave}
                                            disabled={saving}
                                        >
                                            <HStack gap={1}>
                                                {saving ? <Spinner size="xs" /> : <SaveIcon />}
                                                <Text>Save</Text>
                                            </HStack>
                                        </Button>
                                    )}
                                    <IconButton
                                        aria-label="Delete skill"
                                        size="sm"
                                        variant="ghost"
                                        color="var(--text-muted)"
                                        _hover={{ color: 'red.400' }}
                                        onClick={handleDelete}
                                    >
                                        <DeleteIcon />
                                    </IconButton>
                                    <IconButton
                                        aria-label="Toggle AI assistant"
                                        size="sm"
                                        variant={showChat ? 'solid' : 'ghost'}
                                        colorScheme={showChat ? 'green' : undefined}
                                        color={showChat ? undefined : 'var(--text-muted)'}
                                        _hover={{ color: showChat ? undefined : 'green.400' }}
                                        onClick={() => setShowChat(v => !v)}
                                    >
                                        <ChatIcon active={showChat} />
                                    </IconButton>
                                </HStack>
                            </HStack>

                            {/* Tabs */}
                            <HStack gap={0} mt={4}>
                                {(['code', 'definition', 'readme', 'requirements'] as Tab[]).map(tab => {
                                    const label = tab === 'code' ? 'run.py'
                                        : tab === 'definition' ? 'tool.json'
                                        : tab === 'readme' ? 'README.md'
                                        : 'requirements.txt';
                                    return (
                                        <Button
                                            key={tab}
                                            size="sm"
                                            variant="ghost"
                                            borderBottom="2px solid"
                                            borderColor={activeTab === tab ? 'green.400' : 'transparent'}
                                            borderRadius={0}
                                            color={activeTab === tab ? 'var(--text-heading)' : 'var(--text-muted)'}
                                            fontWeight={activeTab === tab ? 'medium' : 'normal'}
                                            onClick={() => setActiveTab(tab)}
                                            px={4}
                                            _hover={{ color: 'var(--text-heading)' }}
                                        >
                                            {label}
                                        </Button>
                                    );
                                })}
                            </HStack>
                        </Box>

                        {/* Tab content */}
                        <Box flex={1} overflow="auto" p={0}>
                            {activeTab === 'code' && (
                                <Box h="100%" minH="300px">
                                    <CodeEditor
                                        value={editCode}
                                        onChange={v => { setEditCode(v); setDirty(true); }}
                                        language="python"
                                    />
                                </Box>
                            )}

                            {activeTab === 'definition' && (
                                <Box p={6}>
                                    <VStack align="stretch" gap={4}>
                                        <Box>
                                            <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" mb={1} textTransform="uppercase">
                                                Description
                                            </Text>
                                            <Textarea
                                                value={editDescription}
                                                onChange={e => { setEditDescription(e.target.value); setDirty(true); }}
                                                fontSize="sm"
                                                bg="var(--bg-input)"
                                                color="var(--text-primary)"
                                                borderColor="var(--border-secondary)"
                                                rows={3}
                                            />
                                        </Box>
                                        <Box>
                                            <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" mb={2} textTransform="uppercase">
                                                Parameters
                                            </Text>
                                            {detail.definition?.parameters?.properties ? (
                                                <VStack align="stretch" gap={2}>
                                                    {Object.entries(detail.definition.parameters.properties).map(([pname, pdef]) => (
                                                        <HStack
                                                            key={pname}
                                                            p={3}
                                                            borderRadius="md"
                                                            bg="var(--bg-active)"
                                                            gap={3}
                                                        >
                                                            <Text fontSize="sm" fontFamily="mono" fontWeight="medium" color="var(--text-heading)" minW="120px">
                                                                {pname}
                                                            </Text>
                                                            <Badge fontSize="2xs" colorScheme="blue" variant="subtle">
                                                                {pdef.type}
                                                            </Badge>
                                                            {detail.definition?.parameters?.required?.includes(pname) && (
                                                                <Badge fontSize="2xs" colorScheme="red" variant="subtle">
                                                                    required
                                                                </Badge>
                                                            )}
                                                            <Text fontSize="xs" color="var(--text-muted)" flex={1}>
                                                                {pdef.description || ''}
                                                            </Text>
                                                        </HStack>
                                                    ))}
                                                </VStack>
                                            ) : (
                                                <Text fontSize="sm" color="var(--text-muted)">No parameters defined</Text>
                                            )}
                                        </Box>
                                        <Box>
                                            <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" mb={1} textTransform="uppercase">
                                                Qualified Name
                                            </Text>
                                            <Text fontSize="sm" fontFamily="mono" color="var(--text-primary)">
                                                {detail.qualified_name}
                                            </Text>
                                        </Box>
                                    </VStack>
                                </Box>
                            )}

                            {activeTab === 'readme' && (
                                <Box p={6}>
                                    <VStack align="stretch" gap={4}>
                                        <Box h="250px" mb={4}>
                                            <CodeEditor
                                                value={editReadme}
                                                onChange={v => { setEditReadme(v); setDirty(true); }}
                                                language="markdown"
                                                minHeight="250px"
                                            />
                                        </Box>
                                        <Box>
                                            <Text fontSize="xs" fontWeight="bold" color="var(--text-muted)" mb={2} textTransform="uppercase">
                                                Preview
                                            </Text>
                                            <Box
                                                p={4}
                                                borderRadius="md"
                                                bg="var(--bg-active)"
                                                border="1px solid"
                                                borderColor="var(--border-secondary)"
                                            >
                                                <Markdown content={editReadme || '*No README content*'} />
                                            </Box>
                                        </Box>
                                    </VStack>
                                </Box>
                            )}

                            {activeTab === 'requirements' && (
                                <Box p={6}>
                                    <VStack align="stretch" gap={4}>
                                        <Text fontSize="xs" color="var(--text-muted)">
                                            pip dependencies (one per line). Installed automatically before the skill runs.
                                        </Text>
                                        <Box h="300px">
                                            <CodeEditor
                                                value={editRequirements}
                                                onChange={v => { setEditRequirements(v); setDirty(true); }}
                                                language="text"
                                                minHeight="300px"
                                            />
                                        </Box>
                                    </VStack>
                                </Box>
                            )}
                        </Box>
                    </>
                ) : (
                    <Box flex={1} display="flex" alignItems="center" justifyContent="center">
                        <Text color="var(--text-muted)">Skill not found</Text>
                    </Box>
                )}
            </Box>

            {/* AI Assistant chat panel */}
            {showChat && (
                <Box
                    w="400px" minW="400px" h="100%"
                    borderLeft="1px solid" borderColor="var(--border-primary)"
                    display="flex" flexDirection="column"
                    bg="var(--bg-page)"
                >
                    <HStack px={4} py={3} borderBottom="1px solid" borderColor="var(--border-primary)" justify="space-between">
                        <HStack gap={2}>
                            <ChatIcon active />
                            <Text fontSize="sm" fontWeight="bold" color="var(--text-heading)">Skill Builder</Text>
                        </HStack>
                        <HStack gap={1}>
                            <IconButton
                                aria-label="New chat"
                                size="xs"
                                variant="ghost"
                                color="var(--text-muted)"
                                _hover={{ color: 'var(--text-heading)' }}
                                onClick={() => setChatKey(k => k + 1)}
                            >
                                <PlusIcon />
                            </IconButton>
                            <IconButton
                                aria-label="Close chat"
                                size="xs"
                                variant="ghost"
                                color="var(--text-muted)"
                                _hover={{ color: 'var(--text-heading)' }}
                                onClick={() => setShowChat(false)}
                            >
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                                    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
                                </svg>
                            </IconButton>
                        </HStack>
                    </HStack>
                    <Box flex={1} minH={0}>
                        <ChatPanel
                            key={`skill-builder-${chatKey}`}
                            conversationId={null}
                            compact
                            ephemeral
                            initialAgent="skill_builder"
                            placeholder="Describe the skill you want to create or modify..."
                            context={chatContext}
                            onResponseComplete={handleChatDone}
                        />
                    </Box>
                </Box>
            )}
        </Box>
    );
};

export default SkillsPage;

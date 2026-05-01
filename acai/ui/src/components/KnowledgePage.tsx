import { useState, useEffect, useCallback, useMemo } from 'react';
import {
    Box, VStack, HStack, Text, Heading, Input, Spinner, IconButton, Badge,
} from '@chakra-ui/react';
import {
    getKnowledgeTree, getKnowledgeDoc, searchKnowledge, deleteKnowledgeDoc, updateKnowledgeDoc,
    type KnowledgeTree, type KnowledgeDoc,
} from '../services/api';
import Markdown from './Markdown';
import CodeEditor from './CodeEditor';

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
const DocIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" opacity={0.6}>
        <path d="M14 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V8l-6-6zm4 18H6V4h7v5h5v11z" />
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
const DeleteIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
        <path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12zM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4z" />
    </svg>
);
const SearchIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" opacity={0.5}>
        <path d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0016 9.5 6.5 6.5 0 109.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z" />
    </svg>
);

type SelectedDoc = { subject: string; subsubject: string; title: string };

const KnowledgePage = () => {
    const [tree, setTree] = useState<KnowledgeTree>({});
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState<Set<string>>(new Set());
    const [selected, setSelected] = useState<SelectedDoc | null>(null);
    const [doc, setDoc] = useState<KnowledgeDoc | null>(null);
    const [docLoading, setDocLoading] = useState(false);
    const [search, setSearch] = useState('');
    const [searchResults, setSearchResults] = useState<KnowledgeDoc[] | null>(null);
    const [searching, setSearching] = useState(false);
    const [editing, setEditing] = useState(false);
    const [editContent, setEditContent] = useState('');

    const refresh = useCallback(() => {
        setLoading(true);
        getKnowledgeTree()
            .then(setTree)
            .catch(() => {})
            .finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        document.title = 'Knowledge — Açaí';
        refresh();
    }, [refresh]);

    const toggleExpand = useCallback((key: string) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(key)) next.delete(key);
            else next.add(key);
            return next;
        });
    }, []);

    const selectDoc = useCallback((subject: string, subsubject: string, title: string) => {
        setSelected({ subject, subsubject, title });
        setEditing(false);
        setDocLoading(true);
        getKnowledgeDoc(subject, subsubject, title)
            .then(d => { setDoc(d); setEditContent(d.content); })
            .catch(() => setDoc(null))
            .finally(() => setDocLoading(false));
    }, []);

    const handleDelete = useCallback(async () => {
        if (!selected) return;
        if (!confirm(`Delete ${selected.subject}/${selected.subsubject}/${selected.title}?`)) return;
        await deleteKnowledgeDoc(selected.subject, selected.subsubject, selected.title);
        setSelected(null);
        setDoc(null);
        refresh();
    }, [selected, refresh]);

    const handleSave = useCallback(async () => {
        if (!selected) return;
        const updated = await updateKnowledgeDoc(selected.subject, selected.subsubject, selected.title, editContent);
        setDoc(updated);
        setEditing(false);
    }, [selected, editContent]);

    const handleSearch = useCallback(async () => {
        if (!search.trim()) { setSearchResults(null); return; }
        setSearching(true);
        try {
            const results = await searchKnowledge(search.trim());
            setSearchResults(results);
        } catch {
            setSearchResults([]);
        } finally {
            setSearching(false);
        }
    }, [search]);

    const docCount = useMemo(() => {
        let n = 0;
        for (const subs of Object.values(tree)) {
            for (const titles of Object.values(subs)) n += titles.length;
        }
        return n;
    }, [tree]);

    const subjects = Object.keys(tree).sort();

    return (
        <Box h="100vh" display="flex" bg="var(--bg-page)">
            {/* Sidebar / tree */}
            <Box
                w="300px" minW="300px" h="100%" overflowY="auto"
                borderRight="1px solid var(--border-primary)"
                bg="var(--bg-surface)"
                display="flex" flexDir="column"
            >
                <Box p={3} borderBottom="1px solid var(--border-primary)">
                    <HStack mb={2}>
                        <Heading size="sm" color="var(--text-primary)" flex={1}>Knowledge</Heading>
                        <Badge fontSize="xs" colorScheme="blue">{docCount}</Badge>
                    </HStack>
                    <HStack gap={1}>
                        <Input
                            size="xs" placeholder="Search..."
                            value={search}
                            onChange={e => { setSearch(e.target.value); if (!e.target.value) setSearchResults(null); }}
                            onKeyDown={e => { if (e.key === 'Enter') handleSearch(); }}
                            bg="var(--bg-input)" borderColor="var(--border-input)"
                            color="var(--text-primary)" fontSize="xs"
                        />
                        <IconButton
                            aria-label="Search" size="xs" variant="ghost"
                            onClick={handleSearch}
                            disabled={searching}
                        >
                            {searching ? <Spinner size="xs" /> : <SearchIcon />}
                        </IconButton>
                    </HStack>
                </Box>

                <Box flex={1} overflowY="auto" p={2}>
                    {loading ? (
                        <Box textAlign="center" py={8}><Spinner size="sm" /></Box>
                    ) : searchResults !== null ? (
                        <VStack align="stretch" gap={0}>
                            <Text fontSize="xs" color="var(--text-tertiary)" mb={1}>
                                {searchResults.length} result{searchResults.length !== 1 ? 's' : ''}
                            </Text>
                            {searchResults.map(r => (
                                <HStack
                                    key={r.path}
                                    px={2} py={1} borderRadius="sm" cursor="pointer"
                                    bg={selected?.title === r.title && selected?.subject === r.subject
                                        ? 'var(--bg-active)' : 'transparent'}
                                    _hover={{ bg: 'var(--bg-hover)' }}
                                    onClick={() => selectDoc(r.subject, r.subsubject, r.title)}
                                >
                                    <DocIcon />
                                    <Box flex={1} minW={0}>
                                        <Text fontSize="xs" color="var(--text-primary)" truncate>{r.title}</Text>
                                        <Text fontSize="10px" color="var(--text-tertiary)">{r.subject}/{r.subsubject}</Text>
                                    </Box>
                                </HStack>
                            ))}
                        </VStack>
                    ) : (
                        <VStack align="stretch" gap={0}>
                            {subjects.map(subject => {
                                const subKey = `s:${subject}`;
                                const isSubjectOpen = expanded.has(subKey);
                                const subsubjects = Object.keys(tree[subject]).sort();
                                return (
                                    <Box key={subject}>
                                        <HStack
                                            px={1} py={0.5} cursor="pointer" borderRadius="sm"
                                            _hover={{ bg: 'var(--bg-hover)' }}
                                            onClick={() => toggleExpand(subKey)}
                                        >
                                            {isSubjectOpen ? <ChevronDown /> : <ChevronRight />}
                                            <FolderIcon open={isSubjectOpen} />
                                            <Text fontSize="xs" fontWeight={600} color="var(--text-primary)" flex={1}>{subject}</Text>
                                            <Text fontSize="10px" color="var(--text-tertiary)">
                                                {subsubjects.reduce((n, ss) => n + tree[subject][ss].length, 0)}
                                            </Text>
                                        </HStack>
                                        {isSubjectOpen && subsubjects.map(sub => {
                                            const ssKey = `ss:${subject}/${sub}`;
                                            const isSubOpen = expanded.has(ssKey);
                                            const titles = tree[subject][sub].sort();
                                            return (
                                                <Box key={sub} pl={4}>
                                                    <HStack
                                                        px={1} py={0.5} cursor="pointer" borderRadius="sm"
                                                        _hover={{ bg: 'var(--bg-hover)' }}
                                                        onClick={() => toggleExpand(ssKey)}
                                                    >
                                                        {isSubOpen ? <ChevronDown /> : <ChevronRight />}
                                                        <FolderIcon open={isSubOpen} />
                                                        <Text fontSize="xs" color="var(--text-primary)" flex={1}>{sub}</Text>
                                                        <Text fontSize="10px" color="var(--text-tertiary)">{titles.length}</Text>
                                                    </HStack>
                                                    {isSubOpen && titles.map(title => {
                                                        const isSel = selected?.subject === subject
                                                            && selected?.subsubject === sub
                                                            && selected?.title === title;
                                                        return (
                                                            <HStack
                                                                key={title} pl={4} px={2} py={0.5}
                                                                cursor="pointer" borderRadius="sm"
                                                                bg={isSel ? 'var(--bg-active)' : 'transparent'}
                                                                _hover={{ bg: 'var(--bg-hover)' }}
                                                                onClick={() => selectDoc(subject, sub, title)}
                                                            >
                                                                <DocIcon />
                                                                <Text fontSize="xs" color="var(--text-primary)" truncate>
                                                                    {title.replace(/-/g, ' ')}
                                                                </Text>
                                                            </HStack>
                                                        );
                                                    })}
                                                </Box>
                                            );
                                        })}
                                    </Box>
                                );
                            })}
                            {subjects.length === 0 && (
                                <Text fontSize="xs" color="var(--text-tertiary)" textAlign="center" py={8}>
                                    No knowledge documents yet.
                                </Text>
                            )}
                        </VStack>
                    )}
                </Box>
            </Box>

            {/* Content pane */}
            <Box flex={1} h="100%" overflowY="auto" display="flex" flexDir="column">
                {selected && doc ? (
                    <>
                        <HStack
                            px={5} py={3}
                            borderBottom="1px solid var(--border-primary)"
                            bg="var(--bg-surface)"
                        >
                            <Box flex={1}>
                                <Heading size="sm" color="var(--text-primary)">
                                    {doc.title.replace(/-/g, ' ')}
                                </Heading>
                                <Text fontSize="xs" color="var(--text-tertiary)">
                                    {doc.subject} / {doc.subsubject}
                                    {' · '}
                                    {new Date(doc.updated_at * 1000).toLocaleString()}
                                </Text>
                            </Box>
                            <HStack gap={1}>
                                {editing ? (
                                    <>
                                        <IconButton
                                            aria-label="Save" size="xs" variant="outline"
                                            colorScheme="green"
                                            onClick={handleSave}
                                        >
                                            <Text fontSize="xs">Save</Text>
                                        </IconButton>
                                        <IconButton
                                            aria-label="Cancel" size="xs" variant="ghost"
                                            onClick={() => { setEditing(false); setEditContent(doc.content); }}
                                        >
                                            <Text fontSize="xs">Cancel</Text>
                                        </IconButton>
                                    </>
                                ) : (
                                    <IconButton
                                        aria-label="Edit" size="xs" variant="ghost"
                                        onClick={() => setEditing(true)}
                                    >
                                        <Text fontSize="xs">Edit</Text>
                                    </IconButton>
                                )}
                                <IconButton
                                    aria-label="Delete" size="xs" variant="ghost"
                                    color="var(--text-danger, red)"
                                    onClick={handleDelete}
                                >
                                    <DeleteIcon />
                                </IconButton>
                            </HStack>
                        </HStack>
                        <Box flex={1} overflowY="auto" p={5}>
                            {docLoading ? (
                                <Box textAlign="center" py={8}><Spinner /></Box>
                            ) : editing ? (
                                <Box h="100%" minH="400px">
                                    <CodeEditor
                                        value={editContent}
                                        onChange={v => setEditContent(v)}
                                        language="markdown"
                                        minHeight="400px"
                                    />
                                </Box>
                            ) : (
                                <Markdown content={doc.content} />
                            )}
                        </Box>
                    </>
                ) : (
                    <Box flex={1} display="flex" alignItems="center" justifyContent="center">
                        <VStack gap={2} color="var(--text-tertiary)">
                            <Text fontSize="lg" opacity={0.3}>
                                <svg width="48" height="48" viewBox="0 0 24 24" fill="currentColor">
                                    <path d="M18 2H6c-1.1 0-2 .9-2 2v16c0 1.1.9 2 2 2h12c1.1 0 2-.9 2-2V4c0-1.1-.9-2-2-2zM6 4h5v8l-2.5-1.5L6 12V4z" />
                                </svg>
                            </Text>
                            <Text fontSize="sm">Select a document to view</Text>
                        </VStack>
                    </Box>
                )}
            </Box>
        </Box>
    );
};

export default KnowledgePage;

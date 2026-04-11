import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Box, VStack, HStack, Text, IconButton } from '@chakra-ui/react';
import { listConversations, deleteConversation, updateConversation } from '../services/api';
import type { ConversationMeta } from '../services/types';
import ChatPanel from './ChatPanel';

const PlusIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
);

const TrashIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14H6L5 6" />
        <path d="M10 11v6" /><path d="M14 11v6" /><path d="M9 6V4h6v2" />
    </svg>
);

const Home = () => {
    const { convId: urlConvId } = useParams<{ convId?: string }>();
    const navigate = useNavigate();

    const [conversations, setConversations] = useState<ConversationMeta[]>([]);
    const [activeConv, setActiveConv] = useState<string | null>(urlConvId || null);

    const refreshList = useCallback(() => {
        listConversations().then(setConversations).catch(() => {});
    }, []);

    useEffect(() => {
        document.title = 'Conversations - ASSAI';
        refreshList();
    }, [refreshList]);

    useEffect(() => {
        setActiveConv(urlConvId || null);
    }, [urlConvId]);

    const selectConversation = useCallback((id: string) => {
        navigate(`/conversations/${id}`);
    }, [navigate]);

    const handleNewConversation = useCallback(() => {
        navigate('/');
    }, [navigate]);

    const handleDeleteConversation = useCallback((id: string) => {
        deleteConversation(id).then(() => {
            if (activeConv === id) {
                navigate('/');
            }
            refreshList();
        }).catch(() => {});
    }, [activeConv, refreshList, navigate]);

    const conv = conversations.find(c => c.id === activeConv);

    return (
        <Box display="flex" h="100vh" w="100%" bg="var(--bg-page)" overflow="hidden">
            {/* Sidebar */}
            <Box
                w="260px" flexShrink={0}
                borderRight="1px solid" borderColor="var(--border-primary)"
                display="flex" flexDirection="column"
                bg="var(--bg-page)"
            >
                <Box px={3} py={3} borderBottom="1px solid" borderColor="var(--border-primary)">
                    <HStack justify="space-between">
                        <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">
                            Conversations
                        </Text>
                        <IconButton
                            aria-label="New conversation"
                            onClick={handleNewConversation}
                            variant="ghost" size="xs" color="var(--text-tertiary)"
                            _hover={{ color: 'var(--text-heading)', bg: 'var(--bg-hover)' }}
                        >
                            <PlusIcon />
                        </IconButton>
                    </HStack>
                </Box>

                <Box flex={1} overflowY="auto" px={2} py={2}>
                    <VStack gap={1} align="stretch">
                        {conversations.length === 0 && (
                            <Text fontSize="xs" color="var(--text-muted)" textAlign="center" py={4}>
                                No conversations yet
                            </Text>
                        )}
                        {conversations.map(c => (
                            <HStack
                                key={c.id}
                                px={3} py={2}
                                borderRadius="md"
                                cursor="pointer"
                                bg={activeConv === c.id ? 'var(--bg-active)' : 'transparent'}
                                _hover={{ bg: activeConv === c.id ? 'var(--bg-active)' : 'var(--bg-hover)' }}
                                onClick={() => selectConversation(c.id)}
                                role="group"
                            >
                                <Text
                                    flex={1}
                                    fontSize="sm"
                                    color={activeConv === c.id ? 'var(--text-heading)' : 'var(--text-tertiary)'}
                                    lineClamp={1}
                                >
                                    {c.title || 'Untitled'}
                                </Text>
                                <IconButton
                                    aria-label="Delete"
                                    onClick={(e) => { e.stopPropagation(); handleDeleteConversation(c.id); }}
                                    variant="ghost" size="xs"
                                    color="var(--text-muted)"
                                    opacity={0}
                                    _groupHover={{ opacity: 1 }}
                                    _hover={{ color: 'red.400', bg: 'transparent' }}
                                >
                                    <TrashIcon />
                                </IconButton>
                            </HStack>
                        ))}
                    </VStack>
                </Box>
            </Box>

            {/* Chat area */}
            <ChatPanel
                conversationId={activeConv}
                onConversationCreated={(id) => {
                    navigate(`/conversations/${id}`, { replace: true });
                    refreshList();
                }}
                initialProvider={conv?.provider || 'auto'}
                initialAgent={conv?.agent || (conv?.project ? (conv?.refiner || 'refiner') : 'default')}
                onProviderChange={(v) => {
                    if (activeConv) updateConversation(activeConv, { provider: v }).catch(() => {});
                }}
                onAgentChange={(v) => {
                    if (activeConv) updateConversation(activeConv, { agent: v }).catch(() => {});
                }}
            />
        </Box>
    );
};

export default Home;

import { useEffect, useState, useMemo } from 'react';
import {
    Box,
    VStack,
    HStack,
    Text,
    Heading,
    Badge,
    Spinner,
    Button,
    IconButton,
    Table,
} from '@chakra-ui/react';
import { assaiAPI } from '../services/api';
import MermaidTreemap from './MermaidTreemap';

// Format bytes to human readable
const formatBytes = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`;
};

// Generate Mermaid treemap definition
const generateTreemapDefinition = (
    repos: CachedRepo[],
    filter: 'model' | 'dataset' | 'all',
    reposByType: Record<string, CachedRepo[]>
): string => {
    if (repos.length === 0) return '';

    // Format size helper - returns size in bytes (octets)
    const formatSizeForTreemap = (bytes: number): number => {
        return bytes; // Return bytes (octets) value
    };

    // Build treemap syntax - using proper Mermaid treemap-beta syntax
    let definition = 'treemap\n';

    // If filtering by a specific type, show only that type
    if (filter !== 'all') {
        const typeLabel = filter.charAt(0).toUpperCase() + filter.slice(1) + 's';
        definition += `\t"${typeLabel}"\n`;
        repos.forEach(repo => {
            // Escape quotes in repo_id and truncate if too long
            const repoId = repo.repo_id.replace(/"/g, '\\"').substring(0, 50);
            // Format size value in bytes (octets), ensure minimum of 1 for visibility
            const sizeValue = Math.max(1, formatSizeForTreemap(repo.size_on_disk));
            // Use tab for indentation (Mermaid treemap requires tabs)
            definition += `\t\t"${repoId}": ${sizeValue}\n`;
        });
    } else {
        // Show all types
        Object.entries(reposByType).forEach(([type, typeRepos]) => {
            const typeLabel = type.charAt(0).toUpperCase() + type.slice(1) + 's';
            definition += `\t"${typeLabel}"\n`;
            typeRepos.forEach(repo => {
                const repoId = repo.repo_id.replace(/"/g, '\\"').substring(0, 50);
                // Format size value in bytes (octets), ensure minimum of 1 for visibility
                const sizeValue = Math.max(1, formatSizeForTreemap(repo.size_on_disk));
                // Use tab for indentation (Mermaid treemap requires tabs)
                definition += `\t\t"${repoId}": ${sizeValue}\n`;
            });
        });
    }

    return definition;
};

// Trash icon for delete button
const TrashIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="3 6 5 6 21 6" />
        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
    </svg>
);

interface CachedRevision {
    commit_hash: string;
    size_on_disk: number;
    nb_files: number;
    last_accessed: number;
    last_modified: number;
}

interface CachedRepo {
    repo_id: string;
    repo_type: string;
    size_on_disk: number;
    nb_files: number;
    revisions: Record<string, CachedRevision>;
    last_accessed: number;
    last_modified: number;
}

interface CacheInfo {
    size_on_disk: number;
    repos: Record<string, CachedRepo>;
}

const HuggingFaceModels = () => {
    const [cacheInfo, setCacheInfo] = useState<CacheInfo | null>(null);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [filter, setFilter] = useState<'model' | 'dataset' | 'all'>('model');

    // Compute derived data - must be before any conditional returns
    const allRepos = cacheInfo?.repos ? Object.values(cacheInfo.repos) : [];
    const filteredRepos = filter === 'all'
        ? allRepos
        : allRepos.filter(repo => repo.repo_type === filter);
    // Sort by size (largest first)
    const repos = useMemo(() => {
        return [...filteredRepos].sort((a, b) => b.size_on_disk - a.size_on_disk);
    }, [filteredRepos]);
    const totalSize = cacheInfo?.size_on_disk || 0;
    const filteredTotalSize = repos.reduce((sum, repo) => sum + repo.size_on_disk, 0);

    // Group repos by type for treemap
    const reposByType = useMemo(() => {
        const grouped: Record<string, CachedRepo[]> = {};
        repos.forEach(repo => {
            const type = repo.repo_type || 'other';
            if (!grouped[type]) {
                grouped[type] = [];
            }
            grouped[type].push(repo);
        });
        return grouped;
    }, [repos]);

    // Generate Mermaid treemap definition - must be before conditional returns
    const treemapDefinition = useMemo(() => {
        if (!cacheInfo || repos.length === 0) return '';
        const definition = generateTreemapDefinition(repos, filter, reposByType);
        console.log('Generated treemap definition:\n', definition);
        return definition;
    }, [cacheInfo, repos, filter, reposByType]);

    useEffect(() => {
        document.title = 'Hugging Face Models - ASSAI';
        loadCache();
    }, []);

    const loadCache = async () => {
        try {
            setIsLoading(true);
            setError(null);
            const data = await assaiAPI.getHuggingFaceCache();
            setCacheInfo(data);
        } catch (err) {
            setError('Failed to load Hugging Face cache');
            console.error('Error loading cache:', err);
        } finally {
            setIsLoading(false);
        }
    };

    const handleDelete = async (repoId: string) => {
        // TODO: Implement delete functionality when backend route is available
        console.log('Delete not yet implemented for:', repoId);
        // For now, just log - delete functionality will be added when backend route is ready
    };

    if (isLoading) {
        return (
            <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                h="100vh"
                bg="gray.900"
            >
                <VStack gap={4}>
                    <Spinner size="xl" color="green.500" />
                    <Text color="gray.300">Loading Hugging Face cache...</Text>
                </VStack>
            </Box>
        );
    }

    if (error) {
        return (
            <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                h="100vh"
                bg="gray.900"
            >
                <VStack gap={4}>
                    <Text color="red.400" fontSize="lg">
                        {error}
                    </Text>
                    <Button onClick={loadCache} colorScheme="green" variant="outline">
                        Retry
                    </Button>
                </VStack>
            </Box>
        );
    }

    return (
        <Box
            p={8}
            maxW="7xl"
            mx="auto"
            bg="gray.900"
            minH="100vh"
            w="100%"
            h="100%"
            overflowY="auto"
        >
            <VStack align="flex-start" gap={6} mb={8}>
                <HStack justify="space-between" w="100%">
                    <VStack align="flex-start" gap={2}>
                        <Heading size="2xl" color="white">
                            Hugging Face Models
                        </Heading>
                        <Text fontSize="lg" color="gray.400">
                            Manage your local Hugging Face model cache
                        </Text>
                    </VStack>
                    <Button
                        onClick={loadCache}
                        colorScheme="green"
                        variant="outline"
                        color="gray.200"
                        borderColor="gray.600"
                        _hover={{ bg: 'gray.700', borderColor: 'gray.500' }}
                    >
                        Refresh
                    </Button>
                </HStack>

                {/* Filter Buttons */}
                <HStack gap={2}>
                    <Button
                        size="sm"
                        variant={filter === 'model' ? 'solid' : 'outline'}
                        colorScheme={filter === 'model' ? 'blue' : 'gray'}
                        onClick={() => setFilter('model')}
                        color={filter === 'model' ? 'white' : 'gray.300'}
                        borderColor="gray.600"
                        px={4}
                        _hover={{ bg: filter === 'model' ? 'blue.600' : 'gray.700' }}
                    >
                        Models
                    </Button>
                    <Button
                        size="sm"
                        variant={filter === 'dataset' ? 'solid' : 'outline'}
                        colorScheme={filter === 'dataset' ? 'green' : 'gray'}
                        onClick={() => setFilter('dataset')}
                        color={filter === 'dataset' ? 'white' : 'gray.300'}
                        borderColor="gray.600"
                        px={4}
                        _hover={{ bg: filter === 'dataset' ? 'green.600' : 'gray.700' }}
                    >
                        Datasets
                    </Button>
                    <Button
                        size="sm"
                        variant={filter === 'all' ? 'solid' : 'outline'}
                        colorScheme={filter === 'all' ? 'purple' : 'gray'}
                        onClick={() => setFilter('all')}
                        color={filter === 'all' ? 'white' : 'gray.300'}
                        borderColor="gray.600"
                        px={4}
                        _hover={{ bg: filter === 'all' ? 'purple.600' : 'gray.700' }}
                    >
                        All
                    </Button>
                </HStack>

                {/* Summary Stats */}
                <Box
                    w="100%"
                    p={4}
                    bg="gray.800"
                    borderRadius="lg"
                    border="1px solid"
                    borderColor="gray.700"
                >
                    <HStack gap={8}>
                        <VStack align="flex-start" gap={1}>
                            <Text fontSize="sm" color="gray.400">
                                {filter === 'all' ? 'Total Cache Size' : `Filtered Cache Size (${filter}s)`}
                            </Text>
                            <Text fontSize="xl" fontWeight="bold" color="white">
                                {formatBytes(filter === 'all' ? totalSize : filteredTotalSize)}
                            </Text>
                            {filter !== 'all' && (
                                <Text fontSize="xs" color="gray.500">
                                    Total: {formatBytes(totalSize)}
                                </Text>
                            )}
                        </VStack>
                        <VStack align="flex-start" gap={1}>
                            <Text fontSize="sm" color="gray.400">
                                {filter === 'model' ? 'Cached Models' : filter === 'dataset' ? 'Cached Datasets' : 'Total Items'}
                            </Text>
                            <Text fontSize="xl" fontWeight="bold" color="white">
                                {repos.length}
                            </Text>
                            {filter !== 'all' && (
                                <Text fontSize="xs" color="gray.500">
                                    Total: {allRepos.length}
                                </Text>
                            )}
                        </VStack>
                    </HStack>
                </Box>
            </VStack>

            {repos.length === 0 ? (
                <Box
                    p={12}
                    textAlign="center"
                    bg="gray.800"
                    borderRadius="lg"
                    border="1px solid"
                    borderColor="gray.700"
                >
                    <Text fontSize="lg" color="gray.400">
                        No models in cache. Models will appear here after downloading.
                    </Text>
                </Box>
            ) : (
                <Box
                    bg="gray.800"
                    borderRadius="lg"
                    border="1px solid"
                    borderColor="gray.700"
                    overflow="hidden"
                >
                    <Table.Root variant="line" size="md" colorPalette="gray" css={{
                        '& th, & td': {
                            borderColor: 'var(--chakra-colors-gray-700) !important',
                        },
                    }}>
                        <Table.Header bg="gray.800">
                            <Table.Row bg="gray.800">
                                <Table.ColumnHeader color="gray.300" fontWeight="semibold" py={4} bg="gray.800" borderColor="gray.700" pl={6}>
                                    Model Name
                                </Table.ColumnHeader>
                                <Table.ColumnHeader color="gray.300" fontWeight="semibold" py={4} bg="gray.800" borderColor="gray.700">
                                    Type
                                </Table.ColumnHeader>
                                <Table.ColumnHeader color="gray.300" fontWeight="semibold" py={4} bg="gray.800" borderColor="gray.700">
                                    Size
                                </Table.ColumnHeader>
                                <Table.ColumnHeader color="gray.300" fontWeight="semibold" py={4} bg="gray.800" borderColor="gray.700">
                                    Files
                                </Table.ColumnHeader>
                                <Table.ColumnHeader color="gray.300" fontWeight="semibold" py={4} bg="gray.800" borderColor="gray.700">
                                    Revisions
                                </Table.ColumnHeader>
                                <Table.ColumnHeader color="gray.300" fontWeight="semibold" py={4} bg="gray.800" borderColor="gray.700">
                                    Last Accessed
                                </Table.ColumnHeader>
                                <Table.ColumnHeader color="gray.300" fontWeight="semibold" py={4} bg="gray.800" borderColor="gray.700" w="80px">
                                    Actions
                                </Table.ColumnHeader>
                            </Table.Row>
                        </Table.Header>
                        <Table.Body>
                            {repos.map((repo) => {
                                const revisionCount = Object.keys(repo.revisions || {}).length;
                                const lastAccessed = repo.last_accessed
                                    ? new Date(repo.last_accessed * 1000).toLocaleDateString()
                                    : 'Never';

                                return (
                                    <Table.Row
                                        key={repo.repo_id}
                                        bg="gray.900"
                                        _hover={{
                                            bg: "gray.800",
                                        }}
                                        transition="all 0.2s"
                                    >
                                        <Table.Cell py={4} bg="gray.900" borderColor="gray.700" pl={6}>
                                            <Text
                                                fontWeight="medium"
                                                color="white"
                                                wordBreak="break-word"
                                            >
                                                {repo.repo_id}
                                            </Text>
                                        </Table.Cell>
                                        <Table.Cell py={4} bg="gray.900" borderColor="gray.700">
                                            <Badge
                                                colorScheme={
                                                    repo.repo_type === 'model' ? 'blue' :
                                                        repo.repo_type === 'dataset' ? 'green' :
                                                            repo.repo_type === 'space' ? 'purple' : 'gray'
                                                }
                                                fontSize="xs"
                                                px={2}
                                                py={1}
                                                variant="solid"
                                            >
                                                {repo.repo_type || 'model'}
                                            </Badge>
                                        </Table.Cell>
                                        <Table.Cell py={4} bg="gray.900" borderColor="gray.700">
                                            <Text color="gray.300">
                                                {formatBytes(repo.size_on_disk)}
                                            </Text>
                                        </Table.Cell>
                                        <Table.Cell py={4} bg="gray.900" borderColor="gray.700">
                                            <Text color="gray.300">
                                                {repo.nb_files}
                                            </Text>
                                        </Table.Cell>
                                        <Table.Cell py={4} bg="gray.900" borderColor="gray.700">
                                            <Text color="gray.300">
                                                {revisionCount}
                                            </Text>
                                        </Table.Cell>
                                        <Table.Cell py={4} bg="gray.900" borderColor="gray.700">
                                            <Text color="gray.300">
                                                {lastAccessed}
                                            </Text>
                                        </Table.Cell>
                                        <Table.Cell py={4} bg="gray.900" borderColor="gray.700">
                                            <IconButton
                                                aria-label="Delete model"
                                                size="sm"
                                                variant="ghost"
                                                colorScheme="red"
                                                onClick={() => handleDelete(repo.repo_id)}
                                                color="red.400"
                                                _hover={{ bg: 'red.900', color: 'red.300' }}
                                            >
                                                <TrashIcon />
                                            </IconButton>
                                        </Table.Cell>
                                    </Table.Row>
                                );
                            })}
                        </Table.Body>
                    </Table.Root>
                </Box>
            )}

            {/* Mermaid Treemap Visualization */}
            {repos.length > 0 && treemapDefinition && (
                <Box mt={8}>
                    <MermaidTreemap
                        definition={treemapDefinition}
                        id={`treemap-${filter}`}
                    />
                </Box>
            )}
        </Box>
    );
};

export default HuggingFaceModels;


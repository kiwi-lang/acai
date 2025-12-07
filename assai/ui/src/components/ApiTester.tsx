import { useState } from 'react';
import {
    Box,
    Button,
    Flex,
    Heading,
    Input,
    Textarea,
    VStack,
    HStack,
    Text,
    IconButton,
    Code,
    Alert,
} from '@chakra-ui/react';

interface QueryParam {
    key: string;
    value: string;
    id: number;
}

const ApiTester = () => {
    const [method, setMethod] = useState<'GET' | 'POST'>('GET');
    const [url, setUrl] = useState('');
    const [queryParams, setQueryParams] = useState<QueryParam[]>([{ key: '', value: '', id: Date.now() }]);
    const [postBody, setPostBody] = useState('');
    const [response, setResponse] = useState<string>('');
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string>('');

    const addQueryParam = () => {
        setQueryParams([...queryParams, { key: '', value: '', id: Date.now() }]);
    };

    const removeQueryParam = (id: number) => {
        setQueryParams(queryParams.filter(param => param.id !== id));
    };

    const updateQueryParam = (id: number, field: 'key' | 'value', newValue: string) => {
        setQueryParams(queryParams.map(param =>
            param.id === id ? { ...param, [field]: newValue } : param
        ));
    };

    const buildUrl = () => {
        let fullUrl = url;
        const validParams = queryParams.filter(p => p.key && p.value);

        if (validParams.length > 0) {
            const queryString = validParams
                .map(p => `${encodeURIComponent(p.key)}=${encodeURIComponent(p.value)}`)
                .join('&');
            fullUrl += `?${queryString}`;
        }

        return fullUrl;
    };

    const executeRequest = async () => {
        setLoading(true);
        setError('');
        setResponse('');

        try {
            const fullUrl = buildUrl();
            const options: RequestInit = {
                method,
                headers: {
                    'Content-Type': 'application/json',
                },
            };

            if (method === 'POST' && postBody) {
                try {
                    JSON.parse(postBody); // Validate JSON
                    options.body = postBody;
                } catch (e) {
                    throw new Error('Invalid JSON in request body');
                }
            }

            const res = await fetch(fullUrl, options);
            const data = await res.json();

            setResponse(JSON.stringify(data, null, 2));
        } catch (err) {
            setError(err instanceof Error ? err.message : 'An error occurred');
        } finally {
            setLoading(false);
        }
    };

    return (
        <Box p={6} bg="gray.900" minH="100vh">
            <Heading mb={6} color="white">API Tester</Heading>

            <VStack gap={4} align="stretch">
                {/* Method and URL */}
                <HStack>
                    <select
                        value={method}
                        onChange={(e: React.ChangeEvent<HTMLSelectElement>) => setMethod(e.target.value as 'GET' | 'POST')}
                        style={{
                            width: '120px',
                            padding: '8px',
                            borderRadius: '6px',
                            border: '1px solid #4A5568',
                            fontSize: '14px',
                            backgroundColor: '#2D3748',
                            color: '#E2E8F0',
                        }}
                    >
                        <option value="GET">GET</option>
                        <option value="POST">POST</option>
                    </select>

                    <Input
                        placeholder="Enter API endpoint (e.g., /api/recipes)"
                        value={url}
                        onChange={(e) => setUrl(e.target.value)}
                        flex={1}
                        bg="gray.700"
                        borderColor="gray.600"
                        color="gray.100"
                        _placeholder={{ color: 'gray.500' }}
                        _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                    />
                </HStack>

                {/* Query Parameters */}
                <Box borderWidth="1px" borderColor="gray.700" borderRadius="md" p={4} bg="gray.800">
                    <Flex justify="space-between" align="center" mb={3}>
                        <Text fontWeight="bold" color="gray.200">Query Parameters</Text>
                        <Button size="sm" colorScheme="blue" onClick={addQueryParam}>
                            Add Parameter
                        </Button>
                    </Flex>

                    <VStack gap={2} align="stretch">
                        {queryParams.map((param) => (
                            <HStack key={param.id}>
                                <Input
                                    placeholder="Key"
                                    value={param.key}
                                    onChange={(e) => updateQueryParam(param.id, 'key', e.target.value)}
                                    size="sm"
                                    bg="gray.700"
                                    borderColor="gray.600"
                                    color="gray.100"
                                    _placeholder={{ color: 'gray.500' }}
                                    _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                                />
                                <Input
                                    placeholder="Value"
                                    value={param.value}
                                    onChange={(e) => updateQueryParam(param.id, 'value', e.target.value)}
                                    size="sm"
                                    bg="gray.700"
                                    borderColor="gray.600"
                                    color="gray.100"
                                    _placeholder={{ color: 'gray.500' }}
                                    _focus={{ borderColor: 'green.500', bg: 'gray.700' }}
                                />
                                <IconButton
                                    aria-label="Remove parameter"
                                    size="sm"
                                    colorScheme="red"
                                    onClick={() => removeQueryParam(param.id)}
                                    disabled={queryParams.length === 1}
                                >
                                    ✕
                                </IconButton>
                            </HStack>
                        ))}
                    </VStack>
                </Box>

                {/* POST Body */}
                {method === 'POST' && (
                    <Box borderWidth="1px" borderColor="gray.700" borderRadius="md" p={4} bg="gray.800">
                        <Text fontWeight="bold" mb={2} color="gray.200">Request Body (JSON)</Text>
                        <Textarea
                            placeholder='{"key": "value"}'
                            value={postBody}
                            onChange={(e) => setPostBody(e.target.value)}
                            rows={6}
                            fontFamily="monospace"
                            bg="gray.700"
                            borderColor="gray.600"
                            color="gray.100"
                            _placeholder={{ color: 'gray.500' }}
                        />
                    </Box>
                )}

                {/* Execute Button */}
                <Button
                    colorScheme="green"
                    onClick={executeRequest}
                    loading={loading}
                    disabled={!url}
                >
                    {loading ? 'Sending...' : 'Send Request'}
                </Button>

                {/* Built URL Preview */}
                {url && (
                    <Box borderWidth="1px" borderColor="gray.700" borderRadius="md" p={3} bg="gray.800">
                        <Text fontSize="sm" fontWeight="bold" mb={1} color="gray.200">Full URL:</Text>
                        <Code colorScheme="gray" p={2} borderRadius="md" display="block" bg="gray.900" color="gray.300">
                            {buildUrl()}
                        </Code>
                    </Box>
                )}

                {/* Error Display */}
                {error && (
                    <Alert.Root status="error">
                        <Alert.Indicator />
                        <Alert.Title>Error</Alert.Title>
                        <Alert.Description>{error}</Alert.Description>
                    </Alert.Root>
                )}

                {/* Response Display */}
                {response && (
                    <Box borderWidth="1px" borderColor="gray.700" borderRadius="md" p={4} bg="gray.800">
                        <Text fontWeight="bold" mb={2} color="gray.200">Response:</Text>
                        <Box
                            bg="gray.900"
                            p={4}
                            borderRadius="md"
                            overflowX="auto"
                            maxHeight="500px"
                            overflowY="auto"
                        >
                            <Code
                                color="green.300"
                                display="block"
                                whiteSpace="pre"
                                fontFamily="monospace"
                                fontSize="sm"
                            >
                                {response}
                            </Code>
                        </Box>
                    </Box>
                )}
            </VStack>
        </Box>
    );
};

export default ApiTester;


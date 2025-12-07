import { HashRouter as Router, Routes, Route } from 'react-router-dom';
import {
  ChakraProvider,
  createSystem,
  defaultConfig,
  Box,
  VStack,
  Heading,
  Text,
} from '@chakra-ui/react';
import { WebSocketProvider } from './contexts/WebSocketContext';
import Layout from './layout/Layout';
import Home from './components/Home';
import ApiTester from './components/ApiTester';
import Models from './components/Models';
import Text2Image from './components/Text2Image';
import HuggingFaceModels from './components/HuggingFaceModels';
import './App.css';

// Create the theme system for Chakra UI v3 with dark mode as default
const system = createSystem(defaultConfig, {
  defaultColorMode: 'dark',
});

// Placeholder component for tasks not yet implemented
const TaskPlaceholder = ({ taskName }: { taskName: string }) => {
  return (
    <Box p={8} maxW="6xl" mx="auto" bg="gray.900" minH="100vh">
      <VStack gap={4} py={12}>
        <Heading size="2xl" color="white">{taskName}</Heading>
        <Text fontSize="lg" color="gray.400">
          This task is coming soon!
        </Text>
      </VStack>
    </Box>
  );
};

function App() {
  return (
    <ChakraProvider value={system}>
      <WebSocketProvider>
        <Router>
          <Layout>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/chat/:conversationId" element={<Home />} />
              <Route path="/text2image" element={<Text2Image />} />
              <Route path="/text2speech" element={<TaskPlaceholder taskName="Text to Speech" />} />
              <Route path="/text2audio" element={<TaskPlaceholder taskName="Text to Audio" />} />
              <Route path="/image2text" element={<TaskPlaceholder taskName="Image to Text" />} />
              <Route path="/speech2text" element={<TaskPlaceholder taskName="Speech to Text" />} />
              <Route path="/models" element={<Models />} />
              <Route path="/huggingface" element={<HuggingFaceModels />} />
              <Route path="/api-tester" element={<ApiTester />} />
            </Routes>
          </Layout>
        </Router>
      </WebSocketProvider>
    </ChakraProvider>
  );
}

export default App;

import { HashRouter as Router, Routes, Route } from 'react-router-dom';
import {
  ChakraProvider,
  createSystem,
  defaultConfig,
  Box,
  VStack,
  Heading,
  Text
} from '@chakra-ui/react';
import Layout from './layout/Layout';
import Home from './components/Home';
import ApiTester from './components/ApiTester';
import Models from './components/Models';
import Text2Image from './components/Text2Image';
import './App.css';

// Create the theme system for Chakra UI v3
const system = createSystem(defaultConfig);

// Placeholder component for tasks not yet implemented
const TaskPlaceholder = ({ taskName }: { taskName: string }) => {
  return (
    <Box p={8} maxW="6xl" mx="auto">
      <VStack gap={4} py={12}>
        <Heading size="2xl">{taskName}</Heading>
        <Text fontSize="lg" color="gray.600">
          This task is coming soon!
        </Text>
      </VStack>
    </Box>
  );
};

function App() {
  return (
    <ChakraProvider value={system}>
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
            <Route path="/api-tester" element={<ApiTester />} />
          </Routes>
        </Layout>
      </Router>
    </ChakraProvider>
  );
}

export default App;

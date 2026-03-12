import sys
from pathlib import Path

import pytest
from unittest.mock import MagicMock

# Add packages directory to path so rag_core is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "packages"))
# Add project root to path so server module is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture
def mock_qdrant_client():
    """Mock Qdrant client for unit tests (no live Qdrant needed)."""
    client = MagicMock()
    client.get_collections.return_value.collections = []
    client.scroll.return_value = ([], None)
    return client


@pytest.fixture
def mock_embedding_service():
    """Mock embedding service (avoids loading the real model in fast tests)."""
    service = MagicMock()
    service.embed.return_value = [0.1] * 384
    service.embed_batch.return_value = [[0.1] * 384]
    return service


@pytest.fixture
def sample_go_code():
    return '''package application

import "context"

// CompanyService handles company operations.
type CompanyService struct {
    repo CompanyRepository
}

// Create creates a new company.
func (s *CompanyService) Create(ctx context.Context, cmd CreateCompanyCommand) (*Company, error) {
    company := NewCompany(cmd.Name)
    return s.repo.Save(ctx, company)
}

// Get retrieves a company by ID.
func (s *CompanyService) Get(ctx context.Context, id string) (*Company, error) {
    return s.repo.FindByID(ctx, id)
}
'''


@pytest.fixture
def sample_vue_code():
    return '''<template>
  <div class="company-form">
    <v-text-field v-model="name" label="Company Name" />
    <v-btn @click="submit">Create</v-btn>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  initialName?: string
}>()

const name = ref(props.initialName ?? '')

function submit() {
  // submit logic
}
</script>

<style scoped>
.company-form {
  padding: 16px;
}
</style>
'''


@pytest.fixture
def sample_markdown():
    return '''# DDD Patterns

## Aggregates

Aggregates are clusters of domain objects.

## Value Objects

Value objects are immutable and defined by their attributes.

## Repositories

Repositories provide access to aggregates.
'''

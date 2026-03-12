from rag_core.chunkers.vue_chunker import VueChunker


def test_splits_sfc_blocks(sample_vue_code):
    chunker = VueChunker()
    chunks = chunker.chunk(sample_vue_code, file_path="CompanyForm.vue")
    types = {c["chunk_type"] for c in chunks}
    assert "template" in types
    assert "script" in types
    assert "style" in types
    assert len(chunks) == 3


def test_template_block_content(sample_vue_code):
    chunker = VueChunker()
    chunks = chunker.chunk(sample_vue_code, file_path="CompanyForm.vue")
    template = next(c for c in chunks if c["chunk_type"] == "template")
    assert "v-text-field" in template["content"]
    assert "v-btn" in template["content"]


def test_script_block_content(sample_vue_code):
    chunker = VueChunker()
    chunks = chunker.chunk(sample_vue_code, file_path="CompanyForm.vue")
    script = next(c for c in chunks if c["chunk_type"] == "script")
    assert "defineProps" in script["content"]


def test_chunk_metadata(sample_vue_code):
    chunker = VueChunker()
    chunks = chunker.chunk(sample_vue_code, file_path="webapp/components/CompanyForm.vue")
    for chunk in chunks:
        assert chunk["language"] == "vue"
        assert chunk["file_path"] == "webapp/components/CompanyForm.vue"


def test_file_without_style():
    content = '''<template><div>Hello</div></template>
<script setup lang="ts">
const msg = "hello"
</script>'''
    chunker = VueChunker()
    chunks = chunker.chunk(content, file_path="Simple.vue")
    assert len(chunks) == 2
    types = {c["chunk_type"] for c in chunks}
    assert "template" in types
    assert "script" in types

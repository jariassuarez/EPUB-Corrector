from epub_corrector.config import CorrectionConfig, GlossaryConfig, ServerConfig


def test_server_config_defaults():
    cfg = ServerConfig()
    assert cfg.base_url == "http://127.0.0.1:1234/v1"
    assert cfg.api_key == "lm-studio"
    assert cfg.model == "local-model"


def test_correction_config_defaults():
    cfg = CorrectionConfig()
    assert cfg.temperature == 0.0
    assert cfg.max_segments_per_request == 1
    assert cfg.max_chars_per_request == 6000
    assert cfg.similarity_threshold == 0.88
    assert cfg.max_change_ratio == 0.20
    assert cfg.use_schema is True


def test_effective_threshold_normal():
    cfg = CorrectionConfig(similarity_threshold=0.75, max_change_ratio=0.30)
    assert cfg.effective_similarity_threshold() == 0.75
    assert cfg.effective_max_change_ratio() == 0.30


def test_effective_threshold_translate():
    cfg = CorrectionConfig(
        similarity_threshold=0.88,
        max_change_ratio=0.20,
        translate=True,
        target_language="French",
    )
    assert cfg.effective_similarity_threshold() == 0.0
    assert cfg.effective_max_change_ratio() == 1.0


def test_effective_threshold_aggressive():
    cfg = CorrectionConfig(
        similarity_threshold=0.88,
        max_change_ratio=0.20,
        aggressive=True,
    )
    assert cfg.effective_similarity_threshold() == 0.0
    assert cfg.effective_max_change_ratio() == 1.0


def test_glossary_config_defaults():
    cfg = GlossaryConfig()
    assert cfg.context_length == 20000

import pytest
from ych_bot.domain.modeling import (
    ChatGenerationRequest,
    ImageGenerationRequest,
    ModelBudgetExceededError,
    ModelCircuitOpenError,
    ModelMessage,
    ModelRequestError,
    ModelResponseError,
    ModelRole,
)
from ych_bot.qualification.fakes import (
    FakeAmbiguousInterruption,
    FakeChatGateway,
    FakeImageGateway,
    FakeProviderBehavior,
    FakeStatsGateway,
    FakeVisionGateway,
)


def _chat_request() -> ChatGenerationRequest:
    return ChatGenerationRequest(
        request_id="fake-1",
        messages=(ModelMessage(role=ModelRole.USER, content="合成测试"),),
    )


@pytest.mark.parametrize(
    "factory",
    [FakeChatGateway, FakeVisionGateway, FakeStatsGateway],
)
@pytest.mark.asyncio
async def test_text_fakes_cover_success_and_classified_failures(factory: type) -> None:
    success = factory(behavior=FakeProviderBehavior.SUCCESS)
    result = await success.generate(_chat_request())
    assert result.text
    assert result.provider_request_id
    assert success.calls == 1

    cases = {
        FakeProviderBehavior.TIMEOUT: TimeoutError,
        FakeProviderBehavior.RETRYABLE: ModelRequestError,
        FakeProviderBehavior.INVALID: ModelResponseError,
        FakeProviderBehavior.QUOTA: ModelBudgetExceededError,
        FakeProviderBehavior.CIRCUIT: ModelCircuitOpenError,
        FakeProviderBehavior.AMBIGUOUS: FakeAmbiguousInterruption,
    }
    for behavior, error in cases.items():
        gateway = factory(behavior=behavior)
        with pytest.raises(error):
            await gateway.generate(_chat_request())
        assert gateway.calls == 1


@pytest.mark.asyncio
async def test_image_fake_covers_success_and_classified_failures() -> None:
    request = ImageGenerationRequest(prompt="合成蓝色方块", request_id="img-1")
    success = FakeImageGateway(behavior=FakeProviderBehavior.SUCCESS)
    result = await success.generate(request)
    assert result.artifacts
    assert result.provider_request_id
    assert success.calls == 1

    cases = {
        FakeProviderBehavior.TIMEOUT: TimeoutError,
        FakeProviderBehavior.RETRYABLE: ModelRequestError,
        FakeProviderBehavior.INVALID: ModelResponseError,
        FakeProviderBehavior.QUOTA: ModelBudgetExceededError,
        FakeProviderBehavior.CIRCUIT: ModelCircuitOpenError,
        FakeProviderBehavior.AMBIGUOUS: FakeAmbiguousInterruption,
    }
    for behavior, error in cases.items():
        gateway = FakeImageGateway(behavior=behavior)
        with pytest.raises(error):
            await gateway.generate(request)
        assert gateway.calls == 1


def test_fake_providers_do_not_share_call_state() -> None:
    chat = FakeChatGateway()
    vision = FakeVisionGateway()
    stats = FakeStatsGateway()
    image = FakeImageGateway()
    assert chat.calls == vision.calls == stats.calls == image.calls == 0

import asyncio
import json
import logging
import os
from functools import cache
from pathlib import Path

os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
os.environ.setdefault("USE_TF", "0")
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import pandas as pd
from graphrag import api
from graphrag.config.models.graph_rag_config import GraphRagConfig
from graphrag_llm.completion import register_completion
from graphrag_llm.completion.lite_llm_completion import LiteLLMCompletion
from graphrag_llm.embedding import LLMEmbedding, register_embedding
from graphrag_llm.types import LLMEmbedding as EmbeddingItem
from graphrag_llm.types import LLMCompletionResponse, LLMEmbeddingResponse, LLMEmbeddingUsage
from graphrag_llm.utils import structure_completion_response

logger = logging.getLogger(__name__)

# GraphRAG 索引输出、缓存、日志都在这里
ROOT = Path(__file__).with_name("rag")
EMBEDDING_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
EMBEDDING_SIZE = 384
COMMUNITY_LEVEL = 2
RESPONSE_TYPE = "multiple paragraphs"


# DeepSeek 只支持 json_object，不支持 json_schema：把 schema 写进提示词，结果再按 pydantic 校验
class DeepSeekCompletion(LiteLLMCompletion):
    def completion(self, /, **kwargs):
        response_format = kwargs.pop("response_format", None)
        if response_format is None:
            return super().completion(**kwargs)
        return structured(super().completion(**json_mode(kwargs, response_format)), response_format)

    async def completion_async(self, /, **kwargs):
        response_format = kwargs.pop("response_format", None)
        if response_format is None:
            return await super().completion_async(**kwargs)
        return structured(await super().completion_async(**json_mode(kwargs, response_format)), response_format)


def json_mode(kwargs, response_format):
    messages = kwargs["messages"]
    if isinstance(messages, str):
        messages = [{"role": "user", "content": messages}]
    schema = json.dumps(response_format.model_json_schema(), ensure_ascii=False)
    kwargs["messages"] = [{"role": "system", "content": f"只输出符合以下 JSON Schema 的 json：\n{schema}"}, *messages]
    kwargs["response_format_json_object"] = True
    return kwargs


def structured(response, response_format):
    if not isinstance(response, LLMCompletionResponse):
        raise ValueError("response_format 不支持流式输出")
    response.formatted_response = structure_completion_response(response.content, response_format)
    return response


@cache
def sentence_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(EMBEDDING_MODEL)


# 本地 sentence-transformers 向量化，DeepSeek 没有 embedding 接口
class LocalEmbedding(LLMEmbedding):
    def __init__(self, *, tokenizer, metrics_store, **kwargs):
        self._tokenizer = tokenizer
        self._metrics_store = metrics_store

    def embedding(self, /, **kwargs):
        vectors = sentence_model().encode(kwargs["input"], normalize_embeddings=True).tolist()
        return LLMEmbeddingResponse(
            object="list",
            data=[EmbeddingItem(object="embedding", embedding=vector, index=i) for i, vector in enumerate(vectors)],
            model=EMBEDDING_MODEL,
            usage=LLMEmbeddingUsage(prompt_tokens=0, total_tokens=0),
        )

    async def embedding_async(self, /, **kwargs):
        return self.embedding(**kwargs)

    @property
    def metrics_store(self):
        return self._metrics_store

    @property
    def tokenizer(self):
        return self._tokenizer


register_completion("deepseek_json", DeepSeekCompletion, scope="singleton")
register_embedding("local_embedding", LocalEmbedding, scope="singleton")

CHAPTER = """“斗之力，三段！”

望着测验魔石碑上面闪亮得甚至有些刺眼的五个大字，少年面无表情，唇角有着一抹自嘲，紧握的手掌，因为大力，而导致略微尖锐的指甲深深的刺进了掌心之中，带来一阵阵钻心的疼痛……

“萧炎，斗之力，三段！级别：低级！”测验魔石碑之旁，一位中年男子，看了一眼碑上所显示出来的信息，语气漠然的将之公布了出来……

中年男子话刚刚脱口，便是不出意外的在人头汹涌的广场上带起了一阵嘲讽的骚动。

“三段？嘿嘿，果然不出我所料，这个‘天才’这一年又是在原地踏步！”

“哎，这废物真是把家族的脸都给丢光了。”

“要不是族长是他的父亲，这种废物，早就被驱赶出家族，任其自生自灭了，哪还有机会待在家族中白吃白喝。”

“唉，昔年那名闻乌坦城的天才少年，如今怎么落魄成这般模样了啊？”

“谁知道呢，或许做了什么亏心事，惹得神灵降怒了吧……”

周围传来的不屑嘲笑以及惋惜轻叹，落在那如木桩待在原地的少年耳中，恍如一根根利刺狠狠的扎在心脏一般，让得少年呼吸微微急促。

少年缓缓抬起头来，露出一张有些清秀的稚嫩脸庞，漆黑的眸子木然的在周围那些嘲讽的同龄人身上扫过，少年嘴角的自嘲，似乎变得更加苦涩了。

“这些人，都如此刻薄势力吗？或许是因为三年前他们曾经在自己面前露出过最谦卑的笑容，所以，如今想要讨还回去吧……”苦涩的一笑，萧炎落寞的转身，安静的回到了队伍的最后一排，孤单的身影，与周围的世界，有些格格不入。

“下一个，萧媚！”

听着测验人的喊声，一名少女快速的人群中跑出，少女刚刚出场，附近的议论声便是小了许多，一双双略微火热的目光，牢牢的锁定着少女的脸颊……

少女年龄不过十四左右，虽然并算不上绝色，不过那张稚气未脱的小脸，却是蕴含着淡淡的妩媚，清纯与妩媚，矛盾的集合，让得她成功的成为了全场瞩目的焦点……

少女快步上前，小手轻车熟路的触摸着漆黑的魔石碑，然后缓缓闭上眼睛……

在少女闭眼片刻之后，漆黑的魔石碑之上再次亮起了光芒……

“斗之气：七段！”

“萧媚，斗之气：七段！级别：高级！”

“耶！”听着测验员所喊出的成绩，少女脸颊扬起了得意的笑容……

“啧啧，七段斗之气，真了不起，按这进度，恐怕顶多只需要三年时间，她就能称为一名真正的斗者了吧……”

“不愧是家族中种子级别的人物啊……”

听着人群中传来的一阵阵羡慕声，少女脸颊上的笑容更是多了几分，虚荣心，这是很多女孩都无法抗拒的诱惑……

与平日里的几个姐妹互相笑谈着，萧媚的视线，忽然的透过周围的人群，停在了人群外的那一道孤单身影上……

皱眉思虑了瞬间，萧媚还是打消了过去的念头，现在的两人，已经不在同一个阶层之上，以萧炎最近几年的表现，成年后，顶多只能作为家族中的下层人员，而天赋优秀的她，则将会成为家族重点培养的强者，前途可以说是不可限量。

“唉……”莫名的轻叹了一口气，萧媚脑中忽然浮现出三年前那意气风发的少年，四岁练气，十岁拥有九段斗之气，十一岁突破十段斗之气，成功凝聚斗之气旋，一跃成为家族百年之内最年轻的斗者！

当初的少年，自信而且潜力无可估量，不知让得多少少女对其春心荡漾，当然，这也包括以前的萧媚。

然而天才的道路，貌似总是曲折的，三年之前，这名声望达到巅峰的天才少年，却是突兀的接受到了有生以来最残酷的打击，不仅辛辛苦苦修炼十数载方才凝聚的斗之气旋，一夜之间，化为乌有，而且体内的斗之气，也是随着时间的流逝，变得诡异的越来越少。

斗之气消失的直接结果，便是导致其实力不断的后退。

从天才的神坛，一夜跌落到了连普通人都不如的地步，这种打击，让得少年从此失魂落魄，天才之名，也是逐渐的被不屑与嘲讽所替代。

站的越高，摔得越狠，这次的跌落，或许就再也没有爬起的机会。

“下一个，萧薰儿！”

喧闹的人群中，测试员的声音，再次响了起来。

随着这有些清雅的名字响起，人群忽然的安静了下来，所有的视线，豁然转移。

在众人视线汇聚之处，一位身着紫色衣裙的少女，正淡雅的站立，平静的稚嫩俏脸，并未因为众人的注目而改变分毫。

少女清冷淡然的气质，犹如清莲初绽，小小年纪，却已初具脱俗气质，难以想象，日后若是长大，少女将会如何的倾国倾城……

这名紫裙少女，论起美貌与气质来，比先前的萧媚，无疑还要更胜上几分，也难怪在场的众人都是这般动作。

莲步微移，名为萧薰儿的少女行到魔石碑之前，小手伸出，镶着黑金丝的紫袖滑落而下，露出一截雪白娇嫩的皓腕，然后轻触着石碑……

微微沉静，石碑之上，刺眼的光芒再次绽放。

“斗之气：九段！级别：高级！”

望着石碑之上的字体，场中陷入了一阵寂静。

“……竟然到九段了，真是恐怖！家族中年轻一辈的第一人，恐怕非薰儿小姐莫属了。”寂静过后，周围的少年，都是不由自主的咽了一口唾沫，眼神充满敬畏……

斗之气，每位斗者的必经之路，初阶斗之气分一至十段，当体内斗之气到达十段之时，便能凝聚斗之气旋，成为一名受人尊重的斗者！

人群中，萧媚皱着浅眉盯着石碑前的紫裙少女，脸颊上闪过一抹嫉妒……

望着石碑上的信息，一旁的中年测验员漠然的脸庞上竟然也是罕见的露出了一丝笑意，对着少女略微恭声道：“薰儿小姐，半年之后，你应该便能凝聚斗气之旋，如果你成功的话，那么以十四岁年龄成为一名真正的斗者，你是萧家百年内的第二人！”

是的，第二人，那位第一人，便是褪去了天才光环的萧炎。

“谢谢。”少女微微点了点头，平淡的小脸并未因为他的夸奖而出现喜悦，安静的回转过身，然后在众人炽热的注目中，缓缓的行到了人群最后面的那颓废少年面前……

“萧炎哥哥。”在经过少年身旁时，少女顿下了脚步，对着萧炎恭敬的弯了弯腰，美丽的俏脸上，居然露出了让周围少女为之嫉妒的清雅笑容。

“我现在还有资格让你怎么叫么？”望着面前这颗已经成长为家族中最璀璨的明珠，萧炎苦涩的道，她是在自己落魄后，极为少数还对自己依旧保持着尊敬的人。

“萧炎哥哥，以前你曾经与薰儿说过，要能放下，才能拿起，提放自如，是自在人！”萧薰儿微笑着柔声道，略微稚嫩的嗓音，却是暖人心肺。

“呵呵，自在人？我也只会说而已，你看我现在的模样，象自在人吗？而且……这世界，本来就不属于我。”萧炎自嘲的一笑，意兴阑珊的道。

面对着萧炎的颓废，萧薰儿纤细的眉毛微微皱了皱，认真的道：“萧炎哥哥，虽然并不知道你究竟是怎么回事，不过，薰儿相信，你会重新站起来，取回属于你的荣耀与尊严……”话到此处，微顿了顿，少女白皙的俏脸，头一次露出淡淡的绯红：“当年的萧炎哥哥，的确很吸引人……”

“呵呵……”面对着少女毫不掩饰的坦率话语，少年尴尬的笑了一声，可却未再说什么，人不风流枉少年，可现在的他，实在没这资格与心情，落寞的回转过身，对着广场之外缓缓行去……

站在原地望着少年那恍如与世隔绝的孤独背影，萧薰儿踌躇了一会，然后在身后一干嫉妒的狼嚎声中，快步追了上去，与少年并肩而行……"""

def config():
    return GraphRagConfig.model_validate({
        "completion_models": {"default_completion_model": {
            "type": "deepseek_json",
            "model_provider": "deepseek",
            "model": os.getenv("MODEL_NAME"),
            "api_base": os.getenv("BASE_URL"),
            "api_key": os.getenv("API_KEY"),
            "call_args": {"temperature": 0},
            "retry": {"type": "exponential_backoff"},
        }},
        "embedding_models": {"default_embedding_model": {
            "type": "local_embedding",
            "model_provider": "local",
            "model": EMBEDDING_MODEL,
        }},
        "chunking": {"type": "tokens", "size": 600, "overlap": 100, "encoding_model": "o200k_base"},
        "extract_graph": {"entity_types": ["person", "organization", "geo", "event", "item"]},
        "output_storage": {"type": "file", "base_dir": str(ROOT / "output")},
        "cache": {"type": "json", "storage": {"type": "file", "base_dir": str(ROOT / "cache")}},
        "reporting": {"type": "file", "base_dir": str(ROOT / "logs")},
        "vector_store": {"type": "lancedb", "db_uri": str(ROOT / "output" / "lancedb"), "vector_size": EMBEDDING_SIZE},
    })


def table(name):
    return pd.read_parquet(ROOT / "output" / f"{name}.parquet")


# 生成：对 CHAPTER 抽实体关系、划社区、写社区报告、建向量索引
def build():
    documents = pd.DataFrame([{
        "id": "chapter-1",
        "title": "斗破苍穹 第一章",
        "text": CHAPTER,
        "creation_date": "",
        "raw_data": None,
        "human_readable_id": 0,
    }])
    results = asyncio.run(api.build_index(config=config(), input_documents=documents))
    failed = [result.workflow for result in results if result.error is not None]
    if failed:
        raise RuntimeError(f"GraphRAG 索引失败: {failed}")
    logger.info("GraphRAG 索引完成: %s", ROOT / "output")


# 查询：global_search 走社区报告 map-reduce，否则走实体 + 原文片段的局部召回
def search(query, global_search=False):
    if global_search:
        response, _ = asyncio.run(api.global_search(
            config=config(),
            entities=table("entities"),
            communities=table("communities"),
            community_reports=table("community_reports"),
            community_level=COMMUNITY_LEVEL,
            dynamic_community_selection=False,
            response_type=RESPONSE_TYPE,
            query=query,
        ))
    else:
        response, _ = asyncio.run(api.local_search(
            config=config(),
            entities=table("entities"),
            communities=table("communities"),
            community_reports=table("community_reports"),
            text_units=table("text_units"),
            relationships=table("relationships"),
            covariates=None,
            community_level=COMMUNITY_LEVEL,
            response_type=RESPONSE_TYPE,
            query=query,
        ))
    return response


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    # build()
    print(search("谁喜欢萧炎？"))

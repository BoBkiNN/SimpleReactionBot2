import disnake
from disnake.ext import commands
import re
from ruamel.yaml import YAML, RoundTripRepresenter
from ruamel.yaml.comments import CommentedMap
from pydantic import BaseModel, Field
from pathlib import Path
from io import StringIO
from typing import Any
import asyncio

CONFIG_PATH = Path("config.yml")

class MessageConfig(BaseModel):
    message_id: int = Field(description="Message ID to listen on")
    emoji_to_role: dict[str, int] = Field(description="Mapping of emoji to role ID. \n"
                                          "Emoji can be a unicode emoji or "
                                          "discord emoji id obtained by using \\:emoji:")

class Config(BaseModel):
    token: str = Field(description="Your bot token")
    messages: list[MessageConfig] = Field(default_factory=list, description=
                                          "List of messages and their emoji-to-role mappings")
    add_reactions: bool = Field(default=True, description=
                                "When set to true, bot will add reactions to messages")

EXAMPLE_CONFIG = Config(
    token="token here",
    messages=[
        MessageConfig(
            message_id=1501995003814215680,
            emoji_to_role={
                "💀": 808081607239008329,
                "☀": 708081607239008329
            }
        ),
        MessageConfig(
            message_id=1502001042731176056,
            emoji_to_role={
                "<:khm:880215367425851393>": 208081607239008329,
                "<:ogoo:880215419586240572>": 508081607239008329
            }
        )
    ],
    add_reactions=True
)

def load_config(file: Path):
    yaml = YAML(typ="safe")
    with file.open(encoding="utf-8") as f:
        d = yaml.load(f)
    return Config.model_validate(d)

def save_example_config(file: Path):
    t = model_to_yaml(EXAMPLE_CONFIG)
    file.write_text(t, encoding="utf-8")

def setup_config(file: Path) -> Config | None:
    if file.exists():
        return load_config(file)
    save_example_config(file)
    return None

bot_config: Config

def get_msg_roles(msg: int):
    for m in bot_config.messages:
        if m.message_id == msg:
            return m.emoji_to_role
    return None


intents = disnake.Intents.default()
intents.reactions = True
bot = commands.InteractionBot(intents=intents)

async def _add_msg_reactions(msg: disnake.Message, emojis: list[str]):
    reacted = [str(r.emoji) for r in msg.reactions if r.me]
    for emoji in emojis:
        if emoji in reacted:
            continue
        print(f"Adding emoji {emoji!r} to message {msg.id}")
        try:
            await msg.add_reaction(emoji)
        except Exception as e:
            print(f"Failed to add emoji {emoji!r} to message {msg.id}: {e}")
    


async def add_reactions():
    print("Adding missing reactions..")
    msgs = bot_config.messages
    for msgc in msgs:
        mid = msgc.message_id
        print(f"Adding missing reactions to message {mid}")
        msg = bot.get_message(mid)
        if not msg:
            print(f"Message {mid} not found. Cannot add reactions to it.")
            continue
        emojis = list(msgc.emoji_to_role.keys())
        asyncio.create_task(_add_msg_reactions(msg, emojis))

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    if bot_config.add_reactions:
        asyncio.create_task(add_reactions())

@bot.event
async def on_raw_reaction_add(payload: disnake.RawReactionActionEvent):
    roles = get_msg_roles(payload.message_id)
    if not roles:
        return
    rid = roles.get(str(payload.emoji))
    if rid == None:
        return
    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return
    message = await channel.fetch_message(payload.message_id) # type: ignore
    if not message:
        return
    guild = message.guild
    if not guild:
        return
    role = guild.get_role(rid)
    if role == None:
        return
    member = await guild.get_or_fetch_member(payload.user_id)
    if member is None:
        return
    await member.add_roles(role, reason=f"Reacted on {message.id}")
    print(f"Given role {rid} to", member.id)

@bot.event
async def on_raw_reaction_remove(payload: disnake.RawReactionActionEvent):
    roles = get_msg_roles(payload.message_id)
    if not roles:
        return
    rid = roles.get(str(payload.emoji))
    if rid == None:
        return
    channel = bot.get_channel(payload.channel_id)
    if not channel:
        return
    message = await channel.fetch_message(payload.message_id)  # type: ignore
    if not message:
        return
    guild = message.guild
    if not guild:
        return
    role = guild.get_role(rid)
    if role == None:
        return
    member = await guild.get_or_fetch_member(payload.user_id)
    if member is None:
        return
    await member.remove_roles(role, reason=f"Unreact {message.id}")
    print(f"Revoked role {rid} from", member.id)

def main():
    global bot_config
    try:
        cfg: Config | None = setup_config(CONFIG_PATH)
    except Exception as e:
        print("Failed to load config:", e)
        exit(1)
    if cfg is None:
        print("Default config generated. Fill it and restart.")
        exit(0)
    bot_config = cfg
    try:
        bot.run(cfg.token)
    except disnake.errors.LoginFailure as e:
        print(f"Failed to login: {e}")

# region yml config

NON_ASCII_OR_SPECIAL = re.compile(r'[^\x20-\x7E]|[:{}\[\],&*#?|\-<>=!%@`]')

class QuotedStringRepresenter(RoundTripRepresenter):
    def represent_str(self, data: str):
        if NON_ASCII_OR_SPECIAL.search(data):
            return self.represent_scalar('tag:yaml.org,2002:str', data, style='"')
        return super().represent_str(data)


QuotedStringRepresenter.add_representer(
    str, QuotedStringRepresenter.represent_str)

def model_to_yaml(model: BaseModel) -> str:
    yaml = YAML()
    yaml.Representer = QuotedStringRepresenter
    yaml.default_flow_style = False
    yaml.indent(mapping=2, sequence=4, offset=2)

    MAPPING_INDENT = 2
    SEQUENCE_OFFSET = 2  # offset = where the key sits relative to the dash

    def build_map(obj: Any, model_cls: type[BaseModel] | None = None, indent: int = 0, is_list_item: bool = False):
        if isinstance(obj, BaseModel):
            model_cls = type(obj)
            cm = CommentedMap()
            for i, (key, field_info) in enumerate(model_cls.model_fields.items()):
                value = getattr(obj, key)

                nested_cls = None
                if isinstance(value, BaseModel):
                    nested_cls = type(value)
                elif isinstance(value, list) and value and isinstance(value[0], BaseModel):
                    nested_cls = type(value[0])

                cm[key] = build_map(value, nested_cls, indent + MAPPING_INDENT)

                if field_info.description:
                    if i == 0 and is_list_item:
                        # First key of a list item: comment on same line
                        cm.yaml_add_eol_comment(field_info.description, key)
                    else:
                        cm.yaml_set_comment_before_after_key(
                            key,
                            before=field_info.description,
                            indent=indent,
                        )

            return cm

        elif isinstance(obj, dict):
            return CommentedMap({k: build_map(v, None, indent + MAPPING_INDENT) for k, v in obj.items()})

        elif isinstance(obj, list):
            return [build_map(v, model_cls, indent + SEQUENCE_OFFSET, is_list_item=True) for v in obj]

        return obj

    data = build_map(model)
    stream = StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()

#endregion

if __name__ == "__main__":
    main()

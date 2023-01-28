#import standart modules
import sys
import os
import random
import asyncio
import itertools
import traceback
from async_timeout import timeout
from functools import partial

#import custom modules
import cod_bot.config

#import special modules
sys.path.append('venv\Lib\site-packages')
import discord
from discord.ext import commands
import yt_dlp
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError
import youtube_search
from config import prefix,configuration
# Suppress noise about console usage from errors
yt_dlp.utils.bug_reports_message = lambda: ''

'''Опции youtube'''
ytdlopts = {
    'format': 'bestaudio/best',
    'postprocessor':[
            {
                'key': 'FFmpegExctractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '256',
            }
                ],
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
        'source_address': '0.0.0.0'  # ipv6 addresses cause issues sometimes
}

'''Опции ffmpeg'''
ffmpegopts = {
    'before_options': '-reconnect 1 -reconnect_at_eof 1 -reconnect_streamed 1 -reconnect_delay_max 2'
                      ' -timeout 2000000000 -y -thread_queue_size 5512 -nostats -nostdin -hide_banner -fflags +genpts -probesize 10000000 -analyzeduration 15000000',
    'options': '-vn'
}

ytdl = YoutubeDL(ytdlopts)

'''Работа с youtube'''
class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        '''Название ролика'''
        self.title = data.get('title')
        '''Ссылка на ролик'''
        self.web_url = data.get('webpage_url')
        '''Время ролика'''
        self.duration = data.get('duration')

    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)


    '''Работа с ссылкой'''
    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        embed = discord.Embed(title="",
                              description=f"Queued [{data['title']}]({data['webpage_url']}) [{ctx.author.mention}]",
                              color=discord.Color.gold())
        await ctx.send(embed=embed)

        '''Перевод времени'''
        seconds = data['duration'] % (24 * 3600)
        hour = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        if hour > 0:
            duration = "%dh %02dm %02ds" % (hour, minutes, seconds)
        else:
            duration = "%02dm %02ds" % (minutes, seconds)

        '''Возвращение данных о ролике'''
        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title'],'duration': duration}

        return cls(discord.FFmpegPCMAudio(source), data=data, requester=ctx.author)

    '''Подготовка потока вместо его загрузки'''
    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)

        return cls(discord.FFmpegPCMAudio(data['url'], **ffmpegopts), data=data, requester=requester)

'''Работа с музыкальныи файлом'''
class FILESource(discord.PCMVolumeTransformer):

    def __init__(self, title, title_name, requester):
        super().__init__(title)
        self.requester = requester
        '''Название ролика'''
        self.title = title
        self.title_name = title_name

    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)


    '''Работа с ссылкой'''
    @classmethod
    async def create_source(cls, ctx, title):
        embed = discord.Embed(title="",
                              description=f"Queued [{title}] [{ctx.author.mention}]",
                              color=discord.Color.gold())
        await ctx.send(embed=embed)

        '''Возвращение данных о ролике'''


        return cls(discord.FFmpegPCMAudio(title),title_name=title, requester=ctx.author)


'''Реализация очереди и цикла'''
class MusicPlayer:
    """Класс, который присваивается каждой гильдии с помощью бота для музыки.
        Этот класс реализует очередь и цикл, которые позволяют разным гильдиям прослушивать разные плейлисты
        одновременно.
        Когда бот отключится от голоса, его экземпляр будет уничтожен.
    """
    '''Разные сервера могут слушать разную музыку'''

    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'np', 'volume')

    def __init__(self, ctx, play_file):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = ctx.cog

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None  # Now playing message
        self.volume = .5
        self.current = None

        if play_file == True:
            ctx.bot.loop.create_task(self.player_loop_file())
        else:
            ctx.bot.loop.create_task(self.player_loop())


    '''Основной цикл(при работе с файлами)'''
    async def player_loop_file(self):

        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()


            try:
                # Wait for the next song. If we timeout cancel the player and disconnect...
                async with timeout(300):  # 5 minutes...
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            '''Вывод играющей песни'''
            self.current = source
            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            embed = discord.Embed(title="Now playing",
                                  description=f"{source.title_name}.",
                                  color=discord.Color.gold())
            print(f"Сейчас будет играть {source.title_name}")
            self.np = await self._channel.send(embed=embed)
            await self.next.wait()

            # Make sure the FFmpeg process is cleaned up.
            '''Очистка ffmpeg процесса после того как песня закончит играть'''
            source.cleanup()
            self.current = None
            play_file = False
            print(f"{source.title_name} закончила играть")

    '''Основной цикл'''
    async def player_loop(self):

        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                # Wait for the next song. If we timeout cancel the player and disconnect...
                async with timeout(300):  # 5 minutes...
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            '''Если поток не был загружен'''
            if not isinstance(source, YTDLSource):
                # Source was probably a stream (not downloaded)
                # So we should regather to prevent stream expiration
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'Произошла ошибка при попытке обработать вашу песню.\n'
                                             f'```css\n[{e}]\n```')
                    continue

            '''Вывод играющей песни'''
            source.volume = self.volume
            self.current = source
            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            embed = discord.Embed(title="Now playing",
                                  description=f"[{source.title}]({source.web_url}) [{source.requester.mention}].",
                                  color=discord.Color.gold())
            print(f'Сейчас будет играть {source.title}')
            self.np = await self._channel.send(embed=embed)
            await self.next.wait()

            # Make sure the FFmpeg process is cleaned up.
            '''Очистка ffmpeg процесса после того как песня закончит играть'''
            source.cleanup()
            self.current = None

            print(f'{source.title} закончила играть')

    '''Отключение основного цикла и завершение работы проигрывателя'''
    def destroy(self, guild):
        """Disconnect and cleanup the player."""
        return self.bot.loop.create_task(self._cog.cleanup(guild))

'''Работа с событиями бота'''
class Events(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

        '''Списки слов на которые отвечает бот'''
        self.nice_words = ['hallo', 'hallo','По приказу генерала Гавса','по приказу генерала Гавса',]
        self.dead_words = ['Я гуль', '1000 - 7', 'Я дед инсайд', 'Я dead inside', '993', 'zxc', 'В паспорте я записан "Кен Канеки"',
                            'Ненавижу позеров как ты']


    '''Запуск бота'''
    @commands.Cog.listener()
    async def on_ready(self):
        print('Bot activated.')
        print('Logged in as ---->', self.bot.user)
        print('ID:', self.bot.user.id)

    '''Обработка сообщений в чатах сервера'''
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.content in self.nice_words:
            await message.channel.send(f"**{self.nice_words[random.randint(0, 9)]}**.")
            await self.bot.process_commands(message)
        elif message.content in self.dead_words:
            await message.channel.send(f"**{self.dead_words[random.randint(0, 7)]}**.")
            await self.bot.process_commands(message)


'''Команды бота'''
class Music(commands.Cog):
    """Команды бота."""

    __slots__ = ('bot', 'players')

    def __init__(self, bot):
        self.bot = bot
        self.players = {}
        self.searchBool = False
        self.results = None
        self.history_of_tracks = list()
        self.skip_counter = None
        self.play_file = False
        # self.repeater = False
        # self.repeaterNum = None


    '''Отключение бота от канала'''
    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    '''Проверка(применяется ко всем командам)'''
    async def __local_check(self, ctx):
        """A local check which applies to all commands in this cog."""
        if not ctx.guild:
            raise commands.NoPrivateMessage
        return True

    '''Обработчик ошибок для всех команд бота'''
    async def __error(self, ctx, error):
        """A local error handler for all errors arising from commands in this cog."""
        if isinstance(error, commands.NoPrivateMessage):
            try:
                return await ctx.send('Эта команда не может использоваться в личных сообщениях.')
            except discord.HTTPException:
                pass
        elif isinstance(error):
            await ctx.send('Ошибка подключения к голосовову каналу. '
                           'Пожалуйста, убедитесь, что вы находитесь в действительном канале или предоставьте боту один из них. ')


        print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    '''Обработчик ошибок для всех команд бота(Если команда введена криво)'''
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            print('Введенная команда не существует')
            embed = discord.Embed(title="", description=f'Такой команды нет.Введите корректную команду.\n'
                                                        f'Чтобы увидеть список команд напишите #help',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''При вызове бота сохраняет данные о вызвашем его'''
    def get_player(self, ctx):
        """Retrieve the guild player, or generate one."""
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            if self.play_file == True:
                player = MusicPlayer(ctx, play_file=self.play_file)
            else:
                player = MusicPlayer(ctx, play_file=False)
            self.players[ctx.guild.id] = player

        return player

    '''Проверка бота на подключение к гс каналу'''
    async def check_connection(self, ctx, vc):
        if not vc or not vc.is_connected():
            print('Бот не подключен к голосовому каналу')
            embed = discord.Embed(title="**Error**", description="Бот не подключен к голосовому каналу.",
                                  color=discord.Color.gold())
            await ctx.send(embed=embed)
            return False
        else:
            return True

    '''Проверка бота на проигрывание песни в гс канале'''
    async def check_playing(self, ctx, player):
        if not player.current:
            print('В данный момент ничего не играет')
            embed = discord.Embed(title="**Error**", description="В данный момент ничего не играет.",
                                  color=discord.Color.gold())
            await ctx.send(embed=embed)
            return False
        else:
            return True


    '''Далее следует список команд
        name  - имя команды
        aliases - псевдонимы команды(их можно вводить вместо команды) 
        description  - короткое пояснение к команде'''

    '''Команда. Подключение бота к гс каналу'''
    @commands.command(name='join', aliases=['connect', 'j', 'add'], description="connects to voice")
    async def connect_(self, ctx, *, channel: discord.VoiceChannel = None):
        """Подключить бота к голосовому каналу."""

        print(f'Command connect_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                print('Ошибка при подключении к голосовому каналу')
                print('Канал для присоединения не обнаружен. Пожалуйста, либо укажите действительный канал, либо присоединитесь к нему.')
                embed = discord.Embed(title="**Error**",
                                      description="Канал для присоединения не обнаружен. Сначала присоединитесь к голосовому каналу. ",
                                      color=discord.Color.gold())
                await ctx.send(embed=embed)
                return False

        vc = ctx.voice_client

        try:
            if vc:
                if vc.channel.id == channel.id:
                    print(f'Ошибка бот уже подключен к каналу "{channel}"')
                    embed = discord.Embed(title="**Error**",
                                          description=f'Бот уже подключен к каналу {channel}.',
                                          color=discord.Color.gold())
                    return await ctx.send(embed=embed)
                try:
                    await vc.move_to(channel)
                    print(f'Бот перешел в канал "{channel}"')
                except asyncio.TimeoutError:
                    await ctx.send(f'Переход к каналу: <{channel}> время ожидания истекло.')
            else:
                try:
                    await channel.connect()
                except asyncio.TimeoutError:
                    await ctx.send(f'Подключение к каналу: <{channel}> истекло время ожидания.')
                    return False
            if (random.randint(0, 1) == 0):
                await ctx.message.add_reaction('👍')
            await ctx.send(f'**Joined to `{channel}`**.')
            print(f'Бот подключен к голосовому каналу "{channel}"')
            return True
        except AttributeError:
            return


    '''Команда. Проигрывание песни'''
    @commands.command(name='play', aliases=['sing', 'p'], description="streams music")
    async def play_(self, ctx, search: str):
        """Добавляет песню в очередь и воспроизводит ее."""

        print(f'Command play_ used by  {ctx.author.name}#{ctx.author.discriminator}')

        await ctx.trigger_typing()

        vc = ctx.voice_client

        if not vc:
            if not await self.connect_(ctx):
                return

        player = self.get_player(ctx)

        '''Обработка ввода при условии поиска песни, а не указании ссылки на неё'''
        if not search.startswith('https://'):
            if self.searchBool == True:
                try:
                    search777 = int(search)
                except ValueError:
                    print('Команда  play_ введена неккоректно')
                    embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                                    f'Укажите  число от 1 до 10 (0 если хотите очистить поиск).',
                                          color=discord.Color.gold())
                    return await ctx.send(embed=embed)

                if not 0 <= search777 <= 10:
                    print('Команда  play_ введена неккоректно')
                    embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                                         f'Укажите  число от 1 до 10 (0 если хотите очистить поиск).',
                                          color=discord.Color.gold())
                    return await ctx.send(embed=embed)

                if search777 == 0 :
                    await ctx.send(f"**Search cleared**.")
                    await ctx.message.delete()
                    print(f'Search cleared')
                    self.searchBool = False
                    self.results = None
                    return

                source = await YTDLSource.create_source(ctx, 'https://www.youtube.com/'+ self.results[search777-1]['url_suffix'], loop=self.bot.loop, download=False)
                self.history_of_tracks.append(source)
                self.searchBool = False
                self.results = None
                await player.queue.put(source)
                return
            self.results = youtube_search.YoutubeSearch(f'{search}', max_results=10).to_dict()
            print('Search results:')
            for x in self.results:
                print('\t\t'+x['title'])
            self.searchBool = True
            embed = discord.Embed(title=f'**Search results** "{search}"',
                                        description=f'Выберите трек командой {cod_bot.config.prefix}play 1 - 10 (0 если хотите очистить поиск).\n'
                                                    f"**1:** {self.results[0]['title']}\n"
                                                    f"**2:** {self.results[1]['title']}\n"
                                                    f"**3:** {self.results[2]['title']}\n"
                                                    f"**4:** {self.results[3]['title']}\n"
                                                    f"**5:** {self.results[4]['title']}\n"
                                                    f"**6:** {self.results[5]['title']}\n"
                                                    f"**7:** {self.results[6]['title']}\n"
                                                    f"**8:** {self.results[7]['title']}\n"
                                                    f"**9:** {self.results[8]['title']}\n"
                                                    f"**10:** {self.results[9]['title']}\n",
                                        color=discord.Color.gold())
            return await ctx.send(embed=embed)

        '''Обработка ввода при условии ввода прямой ссылки на нее'''
        try:
            source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, download=False,)
            self.history_of_tracks.append(source)
        except DownloadError:
                print('Неккоректная ссылка')
                embed = discord.Embed(title="**Error**",
                                            description="Неккоректная ссылка.Введите существующую ссылку.",
                                            color=discord.Color.gold())
                return await ctx.send(embed=embed)

        await player.queue.put(source)
        await ctx.message.delete()

    '''Команда. Установка песни на паузу'''
    @commands.command(name='pause',aliases=['stopsing', 'ps'], description="pauses music")
    async def pause_(self, ctx):
        """Приостанавливает воспроизведение текущей песни."""

        print(f'Command pause_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        player = self.get_player(ctx)

        if not await self.check_connection(ctx,vc):
            return

        elif not await self.check_playing(ctx, player):
            return
        elif vc.is_paused():
            return

        vc.pause()
        await ctx.send("Paused ⏸️.")
        print(f'{vc.source.title} paused')

    '''Команда. Cнятие песни с паузы'''
    @commands.command(name='resume',aliases=['res'], description="resumes music")
    async def resume_(self, ctx):
        """Возобновляет приостановленную в данный момент песню."""

        print(f'Command resume_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        player = self.get_player(ctx)
        if not await self.check_connection(ctx, vc):
            return
        elif not await self.check_playing(ctx, player):
            return
        elif not vc or not vc.is_playing():
            vc.resume()
            await ctx.send("Resuming ⏯️.")
            print(f'{vc.source.title} resuming')
        elif vc or vc.is_playing():
            print('Музыка уже играет либо в очереди нет треков')
            embed = discord.Embed(title="**Error**", description="Музыка уже играет либо в очереди нет треков.",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)
        elif not vc.is_paused():
            return

    '''Команда. Пропуск трека(либо нескольких)'''
    @commands.command(name='skip',aliases=['s', 'stop'], description="skips to next song in queue")
    async def skip_(self, ctx, parameters: str = None):
        """Скипает трек(либо несколько)."""

        print(f'Command skip_ used by {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        player = self.get_player(ctx)
        if not await self.check_connection(ctx, vc):
            return
        if vc.is_paused():
            pass
        elif not await self.check_playing(ctx, player):
            return
        if parameters == None:
            await ctx.send(f"**{vc.source.title}** skipped.")
            print(f'{vc.source.title} skipped')
            vc.stop()
            return
        else:
            try:
                param = int(parameters)
            except ValueError:
                print('Команда skip_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                                     f'Количество треков должно быть натуральным числом.',
                                      color=discord.Color.gold())
                return await ctx.send(embed=embed)
            if param < 0:
                print('Команда skip_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                                     f'Количество треков должно быть натуральным числом.',
                                      color=discord.Color.gold())
                return await ctx.send(embed=embed)
        self.skip_counter= int(param - 1)
        if self.skip_counter > 0:
            while self.skip_counter > 0:
                await self.remove_(ctx,'1')
                self.skip_counter -= 1
                print('skip count -1')

        await ctx.send(f"**{vc.source.title}** skipped.")
        print(f'{vc.source.title} skipped')
        vc.stop()

    '''Команда. Удаление трека из очереди с указанной позиции'''
    @commands.command(name='remove', aliases=['rm', 'rem'], description="removes specified song from queue")
    async def remove_(self, ctx, position: str = None):
        """Удаляет указанную песню из очереди"""

        print(f'Command remove_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        if position == None:
            raise commands.CommandInvokeError
        else:
            try:
                pos = int(position)
            except ValueError:
                print('Команда remove_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                            f'Номер трека должен быть натуральным числом.',
                                      color = discord.Color.gold())
                return await ctx.send(embed=embed)
            if pos < 0:
                print('Команда remove_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                            f'Номер трека должен быть натуральным числом.',
                                      color=discord.Color.gold())
                return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if pos == None:
            player.queue._queue.pop()
        else:
            try:
                s = player.queue._queue[pos - 1]
                del player.queue._queue[pos - 1]
                embed = discord.Embed(title="",
                                      description=f"Removed [{s['title']}]({s['webpage_url']}) [{s['requester'].mention}].",
                                      color=discord.Color.gold())
                await ctx.send(embed=embed)
                print(f'{vc.source.title} removed')
            except:
                embed = discord.Embed(title="**Error**", description=f'Не удалось найти трек на "{pos}" позиции.',
                                      color=discord.Color.gold())
                await ctx.send(embed=embed)
                print(f'Не удалось найти трек на "{pos}" позиции')

    '''Команда. Очистка всей очереди треков'''
    @commands.command(name='clear', aliases=['clr', 'cl', 'cr'], description="clears entire queue")
    async def clear_(self, ctx):
        """Удаляет все песни из очереди кроме последней."""

        print(f'Command clear_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        player = self.get_player(ctx)
        if not player.current:
            print('Очередь пуста')
            embed = discord.Embed(title="**Error**", description="Очередь пуста.",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        player.queue._queue.clear()
        await ctx.send('**Queue cleared**.')
        print('Очередь очищена')

    '''Команда. Вывод списка треков в очереди'''
    @commands.command(name='queue', aliases=['q', 'playlist', 'que'], description="shows the queue")
    async def queue_info(self, ctx):
        """Выводит очередь на воспроизведение треков."""

        print(f'Command queue_info used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        player = self.get_player(ctx)
        if player.queue.empty():
            embed = discord.Embed(title="**Error**", description="Очередь пуста.", color=discord.Color.gold())
            return await ctx.send(embed=embed)

        '''Перевод времени'''
        seconds = vc.source.duration % (24 * 3600)
        hour = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        if hour > 0:
            duration = "%dh %02dm %02ds" % (hour, minutes, seconds)
        else:
            duration = "%02dm %02ds" % (minutes, seconds)

        upcoming = list(itertools.islice(player.queue._queue, 0, int(len(player.queue._queue))))
        print(upcoming)
        fmt = '\n'.join(
            f"`{(upcoming.index(_)) + 1}.` [{_['title']}]({_['webpage_url']}) | ` {_['duration']} Requested by: {_['requester']}`\n"
            for _ in upcoming)
        fmt = f"\n__Now Playing__:\n[{vc.source.title}]({vc.source.web_url}) | ` {duration} Requested by: {vc.source.requester}`\n\n__Up Next:__\n" + fmt + f"\n**{len(upcoming)} songs in queue**"
        embed = discord.Embed(title=f'Queue for {ctx.guild.name}',
                              description=fmt,
                              color=discord.Color.gold())
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.avatar_url)

        await ctx.send(embed=embed)

    '''Команда. Вывод информации о играющем треке'''
    @commands.command(name='np', aliases=['song', 'current', 'currentsong', 'playing', 'nowplaying'], description="shows the current playing song")
    async def now_playing_(self, ctx):
        """Выводит информацию о воспроизводимой в данный момент песне."""

        print(f'Command now_playing_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        player = self.get_player(ctx)
        if not await self.check_playing(ctx, player):
            return

        seconds = vc.source.duration % (24 * 3600)
        hour = seconds // 3600
        seconds %= 3600
        minutes = seconds // 60
        seconds %= 60
        if hour > 0:
            duration = "%dh %02dm %02ds" % (hour, minutes, seconds)
        else:
            duration = "%02dm %02ds" % (minutes, seconds)

        embed = discord.Embed(title="",
                              description=f"[{vc.source.title}]({vc.source.web_url}) [{vc.source.requester.mention}] | `{duration}`",
                              color=discord.Color.gold())
        embed.set_author(icon_url=self.bot.user.avatar_url, name=f"Now Playing 🎶")
        await ctx.send(embed=embed)
        print(f"Now Playing {vc.source.title}({vc.source.web_url}) [{ctx.author.name}#{ctx.author.discriminator}] | `{duration}`")

    '''Команда. Установка громкости воспроизводимой музыки'''
    @commands.command(name='volume', aliases=['vol', 'v'], description="changes Kermit's volume")
    async def change_volume(self, ctx, *, vol: str = None):
        """Устанавливает громкость воспроизведения музыки(1-100)."""

        print(f'Command change_volume used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        try:
            vol = float(vol)
        except ValueError:
            print('Команда remove_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                        f'Значение громкости должно быть числом.',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        if vol is None:
            embed = discord.Embed(title="", description=f"🔊 **{(vc.source.volume) * 100}%**",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        if not 0 < vol < 101:
            print("Введено значение неккоректное значение громкости")
            embed = discord.Embed(title="**Error**", description="Пожалуйста, введите значение от 1 до 100",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)

        if vc.source:
            vc.source.volume = vol / 100

        player.volume = vol / 100
        embed = discord.Embed(title="", description=f'**`{ctx.author}`** set the volume to **{vol}%**',
                              color=discord.Color.gold())
        await ctx.send(embed=embed)
        print(f'**`{ctx.author}`** set the volume to **{vol}%**')

    '''Команда. Кик бота из гс канала'''
    @commands.command(name='leave', aliases=['dc', 'disconnect', 'bye', 'discon'], description="stops music and disconnects from voice")
    async def leave_(self, ctx):
        """Убирает бота из голосового канала."""
        """!Warning!
            This will destroy the player assigned to your guild, also deleting any queued songs and settings.
        """
        print(f'Command leave_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        channel = ctx.author.voice.channel
        if not await self.check_connection(ctx, vc):
            return

        if (random.randint(0, 1) == 0):
            await ctx.message.add_reaction('👋')
        await ctx.send('**Successfully disconnected**')
        print(f'Бот был убран из канала "{channel}"')

        await self.cleanup(ctx.guild)

    '''В разработке. Команда. Установка песни на режим повторения'''
    '''@commands.command(name='repeat', aliases=['rep'], description="repeat song")
    async def repeat_(self, ctx, parameters: str = None):
        """**В разработке**. Повторяет песню заново"""
        print(f'Command repeat_ used by  {ctx.author.name}#{ctx.author.discriminator}')

        vc = ctx.voice_client
        player = self.get_player(ctx)

        if not await self.check_connection(ctx, vc):
            return
        if vc.is_paused():
            pass
        elif not await self.check_playing(ctx, player):
            return
        if parameters == None:
            raise commands.CommandInvokeError
        else:
            try:
                param = int(parameters)
            except ValueError:
                print('Команда repeat_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                            f'Укажите число повторений трека либо поставьте его на бесконечное повторение написав 0.',
                                      color = discord.Color.gold())
                return await ctx.send(embed=embed)
            if param < 0:
                print('Команда repeat_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                            f'Число повторений трека должно быть натуральным числом.',
                                      color=discord.Color.gold())
                return await ctx.send(embed=embed)
        if param == 0:
            self.repeater = True
        else:
            self.repeater = True
            self.repeaterNum = param
        print(f"Was repeat {vc.source.title}({vc.source.web_url}) by  {ctx.author.name}#{ctx.author.discriminator}")

        # vc.stop()
    '''

    '''Команда. Вывод истории всех проигранных треков'''
    @commands.command(name='history', aliases=['h', 'hyi', 'his', 'story'], description="history song")
    async def history_(self, ctx):
        """Показывает историю проигранных треков"""
        print(f'Command history_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        #print("History tracks:\n"+'\t\t'+ str(self.history_of_tracks))
        history =f'\n'.join(
                 f"`{(self.history_of_tracks.index(_)) + 1}.` [{_['title']}]({_['webpage_url']}) | ` {_['duration']} Requested by: {_['requester']}`\n"
            for _ in self.history_of_tracks)
        history =f"\n" + history + f"\n**{len(self.history_of_tracks)} songs in history**"
        embed = discord.Embed(title=f'History track for {ctx.guild.name}', description=history, color=discord.Color.gold())
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.avatar_url)

        await ctx.send(embed=embed)

    '''В разработке. Команда. Воспроизведение трека с начала'''
    @commands.command(name='restart', aliases=["rest"], description="restart playing song")
    async def restart_(self, ctx):
        """**В разработке**.Заново воспроизводит трек"""
        print(f'Command restart_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        player = self.get_player(ctx)
        if not await self.check_playing(ctx, player):
            return
        self.repeater = True
        source = vc.source.web_url
        vc.stop()
        await self.play_(ctx,source)


    '''Далее следует список обрабатываемых ошибок при вызове команд'''

    '''Обработка ошибки от команды play_'''
    @play_.error
    async def play_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            print('Команда play_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно, используйте синтаксис.\n'
                                                        f'{cod_bot.config.prefix}play <url>',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''Обработка ошибки от команды connect_'''
    @connect_.error
    async def connect_error(self, ctx, error):
        if isinstance(error, commands.ChannelNotFound):
            print('Команда connect_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                        f'Вы должны ввести существующее название голосового канала.',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''Обработка ошибки от команды remove_'''
    @remove_.error
    async def remove_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            print('Команда remove_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                        f'Укажите номер трека в очереди.',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''Обработка ошибки от команды change_volume'''
    @change_volume.error
    async def change_volume_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            print('Команда change_volume введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно, используйте синтаксис.\n'
                                                        f'{cod_bot.config.prefix}volume <integer>',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''Обработка ошибки от команды repeat_'''
    '''@repeat_.error
    async def repeat_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            print('Команда repeat_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                        f'Укажите число повторений.',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)
    '''

'''Команды бота(версия файловая)'''
class MusicF(commands.Cog):
    """Команды бота."""

    __slots__ = ('bot', 'players')

    def __init__(self, bot):
        self.bot = bot
        self.players = {}
        self.searchBool = False
        self.results = None
        self.history_of_tracks = list()
        self.skip_counter = None
        self.play_file = True
       # self.file = None
        # self.repeater = False
        # self.repeaterNum = None


    '''Отключение бота от канала'''
    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            return print('Бот отключен во время проигрывания песни!')


        try:
            del self.players[guild.id]
        except KeyError:
            pass

    '''Проверка(применяется ко всем командам)'''
    async def __local_check(self, ctx):
        """A local check which applies to all commands in this cog."""
        if not ctx.guild:
            raise commands.NoPrivateMessage
        return True

    '''Обработчик ошибок для всех команд бота'''
    async def __error(self, ctx, error):
        """A local error handler for all errors arising from commands in this cog."""
        if isinstance(error, commands.NoPrivateMessage):
            try:
                return await ctx.send('Эта команда не может использоваться в личных сообщениях.')
            except discord.HTTPException:
                pass
        elif isinstance(error):
            await ctx.send('Ошибка подключения к голосовову каналу. '
                           'Пожалуйста, убедитесь, что вы находитесь в действительном канале или предоставьте боту один из них. ')


        print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    '''Обработчик ошибок для всех команд бота(Если команда введена криво)'''
    async def on_command_error(self, ctx, error):
        if isinstance(error, commands.CommandNotFound):
            print('Введенная команда не существует')
            embed = discord.Embed(title="", description=f'Такой команды нет.Введите корректную команду.\n'
                                                        f'Чтобы увидеть список команд напишите #help',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''При вызове бота сохраняет данные о вызвашем его'''
    def get_player(self, ctx):
        """Retrieve the guild player, or generate one."""
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            if self.play_file == True:
                player = MusicPlayer(ctx, play_file=self.play_file)
            else:
                player = MusicPlayer(ctx, play_file=False)
            self.players[ctx.guild.id] = player

        return player

    '''Проверка бота на подключение к гс каналу'''
    async def check_connection(self, ctx, vc):
        if not vc or not vc.is_connected():
            print('Бот не подключен к голосовому каналу')
            embed = discord.Embed(title="**Error**", description="Бот не подключен к голосовому каналу.",
                                  color=discord.Color.gold())
            await ctx.send(embed=embed)
            return False
        else:
            return True

    '''Проверка бота на проигрывание песни в гс канале'''
    async def check_playing(self, ctx, player):
        if not player.current:
            print('В данный момент ничего не играет')
            embed = discord.Embed(title="**Error**", description="В данный момент ничего не играет.",
                                  color=discord.Color.gold())
            await ctx.send(embed=embed)
            return False
        else:
            return True


    '''Далее следует список команд
        name  - имя команды
        aliases - псевдонимы команды(их можно вводить вместо команды 
        description  - короткое пояснение к команде'''

    '''Команда. Подключение бота к гс каналу'''
    @commands.command(name='join', aliases=['connect', 'j', 'add'], description="connects to voice")
    async def connect_(self, ctx, *, channel: discord.VoiceChannel = None):
        """Подключить бота к голосовому каналу."""

        print(f'Command connect_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                print('Ошибка при подключении к голосовому каналу')
                print('Канал для присоединения не обнаружен. Пожалуйста, либо укажите действительный канал, либо присоединитесь к нему.')
                embed = discord.Embed(title="**Error**",
                                      description="Канал для присоединения не обнаружен. Сначала присоединитесь к голосовому каналу. ",
                                      color=discord.Color.gold())
                await ctx.send(embed=embed)
                return False

        vc = ctx.voice_client

        try:
            if vc:
                if vc.channel.id == channel.id:
                    print(f'Ошибка бот уже подключен к каналу "{channel}"')
                    embed = discord.Embed(title="**Error**",
                                          description=f'Бот уже подключен к каналу {channel}.',
                                          color=discord.Color.gold())
                    return await ctx.send(embed=embed)
                try:
                    await vc.move_to(channel)
                    print(f'Бот перешел в канал "{channel}"')
                except asyncio.TimeoutError:
                    await ctx.send(f'Переход к каналу: <{channel}> время ожидания истекло.')
            else:
                try:
                    await channel.connect()
                except asyncio.TimeoutError:
                    await ctx.send(f'Подключение к каналу: <{channel}> истекло время ожидания.')
                    return False
            if (random.randint(0, 1) == 0):
                await ctx.message.add_reaction('👍')
            await ctx.send(f'**Joined to `{channel}`**.')
            print(f'Бот подключен к голосовому каналу "{channel}"')
            return True
        except AttributeError:
            return


    '''Команда. Проигрывание песни'''
    @commands.command(name='playF', aliases=['singF', 'pF'], description="streams music")
    async def playF_(self, ctx, search: str):
        """Добавляет песню в очередь и воспроизводит ее."""

        print(f'Command playF_ used by {ctx.author.name}#{ctx.author.discriminator}')

        await ctx.trigger_typing()

        self.play_file = True
        search = 'tracks/' + search

        vc = ctx.voice_client

        if not os.path.isfile(search):
            print('Несуществующее имя файла либо путь к нему')
            embed = discord.Embed(title="**Error**",
                                  description="Данного файла нет в директории, проверьте наличие вашего файла в папке tracks и укажите расширение файла.",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        if not vc:
            if not await self.connect_(ctx):
                return

        player = self.get_player(ctx)

        file = await FILESource.create_source(ctx, title= search)
        self.history_of_tracks.append(file)
        await player.queue.put(file)
        await ctx.message.delete()

    '''Команда. Установка песни на паузу'''
    @commands.command(name='pause',aliases=['stopsing', 'ps'], description="pauses music")
    async def pause_(self, ctx):
        """Приостанавливает воспроизведение текущей песни."""

        print(f'Command pause_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        player = self.get_player(ctx)

        if not await self.check_connection(ctx, vc):
            return

        elif not await self.check_playing(ctx, player):
            return
        elif vc.is_paused():
            return

        vc.pause()
        await ctx.send("Paused ⏸️.")
        print(f"{vc.source.title_name} paused")

    '''Команда. Cнятие песни с паузы'''
    @commands.command(name='resume',aliases=['res'], description="resumes music")
    async def resume_(self, ctx):
        """Возобновляет приостановленную в данный момент песню."""

        print(f'Command resume_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        player = self.get_player(ctx)
        if not await self.check_connection(ctx, vc):
            return
        elif not await self.check_playing(ctx, player):
            return
        elif not vc or not vc.is_playing():
            vc.resume()
            await ctx.send("Resuming ⏯️.")
            print(f"{vc.source.title_name} resuming")
        elif vc or vc.is_playing():
            print('Музыка уже играет либо в очереди нет треков')
            embed = discord.Embed(title="**Error**", description="Музыка уже играет либо в очереди нет треков.",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)
        elif not vc.is_paused():
            return

    '''Команда. Пропуск трека(либо нескольких)'''
    @commands.command(name='skip',aliases=['s', 'stop'], description="skips to next song in queue")
    async def skip_(self, ctx, parameters: str = None):
        """Скипает трек(либо несколько)."""

        print(f'Command skip_ used by {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        player = self.get_player(ctx)
        if not await self.check_connection(ctx, vc):
            return
        if vc.is_paused():
            pass
        elif not await self.check_playing(ctx, player):
            return
        if parameters == None:
            await ctx.send(f"**{vc.source.title_name}** skipped.")
            print(f"{vc.source.title_name} skipped")
            vc.stop()
            return
        else:
            try:
                param = int(parameters)
            except ValueError:
                print('Команда skip_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                                     f'Количество треков должно быть натуральным числом.',
                                      color=discord.Color.gold())
                return await ctx.send(embed=embed)
            if param < 0:
                print('Команда skip_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                                     f'Количество треков должно быть натуральным числом.',
                                      color=discord.Color.gold())
                return await ctx.send(embed=embed)
        self.skip_counter= int(param - 1)
        if self.skip_counter > 0:
            while self.skip_counter > 0:
                await self.remove_(ctx,'1')
                self.skip_counter -= 1
                print('skip count -1')

        await ctx.send(f"**{vc.source.title_name}** skipped.")
        print(f"{vc.source.title_name} skipped")
        vc.stop()

    '''Команда. Удаление трека из очереди с указанной позиции'''
    @commands.command(name='remove', aliases=['rm', 'rem'], description="removes specified song from queue")
    async def remove_(self, ctx, position: str = None):
        """Удаляет указанную песню из очереди"""

        print(f'Command remove_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        if position == None:
            raise commands.CommandInvokeError
        else:
            try:
                pos = int(position)
            except ValueError:
                print('Команда remove_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                            f'Номер трека должен быть натуральным числом.',
                                      color = discord.Color.gold())
                return await ctx.send(embed=embed)
            if pos < 0:
                print('Команда remove_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                            f'Номер трека должен быть натуральным числом.',
                                      color=discord.Color.gold())
                return await ctx.send(embed=embed)

        player = self.get_player(ctx)
        if pos == None:
            player.queue._queue.pop()
        else:
            try:
                s = player.queue._queue[pos - 1]
                del player.queue._queue[pos - 1]
                embed = discord.Embed(title="",
                                      description=f"Removed [{s.title_name}][{s.requester.mention}].",
                                      color=discord.Color.gold())
                await ctx.send(embed=embed)
                print(f"{vc.source.title_name} removed")
            except:
                embed = discord.Embed(title="**Error**", description=f'Не удалось найти трек на "{pos}" позиции.',
                                      color=discord.Color.gold())
                await ctx.send(embed=embed)
                print(f'Не удалось найти трек на "{pos}" позиции')

    '''Команда. Очистка всей очереди треков'''
    @commands.command(name='clear', aliases=['clr', 'cl', 'cr'], description="clears entire queue")
    async def clear_(self, ctx):
        """Удаляет все песни из очереди кроме последней."""

        print(f'Command clear_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        player = self.get_player(ctx)
        if not player.current:
            print('Очередь пуста')
            embed = discord.Embed(title="**Error**", description="Очередь пуста.",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        player.queue._queue.clear()
        await ctx.send('**Queue cleared**.')
        print('Очередь очищена')

    '''Команда. Вывод списка треков в очереди'''
    @commands.command(name='queue', aliases=['q', 'playlist', 'que'], description="shows the queue")
    async def queue_info(self, ctx):
        """Выводит очередь на воспроизведение треков."""

        print(f'Command queue_info used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        player = self.get_player(ctx)
        if player.queue.empty():
            embed = discord.Embed(title="**Error**", description="Очередь пуста.", color=discord.Color.gold())
            return await ctx.send(embed=embed)

        upcoming = list(itertools.islice(player.queue._queue, 0, int(len(player.queue._queue))))
        print(upcoming)
        fmt = '\n'.join(
            f"`{(upcoming.index(_)) + 1}.` [{_.title_name}] | `Requested by: {_.requester}`\n"
            for _ in upcoming)
        fmt = f"\n__Now Playing__:\n[{vc.source.title_name}] | ` Requested by: {vc.source.requester}`\n\n__Up Next:__\n" + fmt + f"\n**{len(upcoming)} songs in queue**"
        embed = discord.Embed(title=f'Queue for {ctx.guild.name}',
                              description=fmt,
                              color=discord.Color.gold())
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.avatar_url)

        await ctx.send(embed=embed)

    '''Команда. Вывод информации о играющем треке'''
    @commands.command(name='np', aliases=['song', 'current', 'currentsong', 'playing', 'nowplaying'], description="shows the current playing song")
    async def now_playing_(self, ctx):
        """Выводит информацию о воспроизводимой в данный момент песне."""

        print(f'Command now_playing_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        player = self.get_player(ctx)
        if not await self.check_playing(ctx, player):
            return

        embed = discord.Embed(title="",
                              description=f"[{vc.source.title_name}] [{vc.source.requester.mention}]",
                              color=discord.Color.gold())
        embed.set_author(icon_url=self.bot.user.avatar_url, name=f"Now Playing 🎶")
        await ctx.send(embed=embed)
        print(f"Now Playing {vc.source.title_name} [{ctx.author.name}#{ctx.author.discriminator}]")

    '''Команда. Установка громкости воспроизводимой музыки'''
    @commands.command(name='volume', aliases=['vol', 'v'], description="changes Kermit's volume")
    async def change_volume(self, ctx, *, vol: str = None):
        """Устанавливает громкость воспроизведения музыки(1-100)."""

        print(f'Command change_volume used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        try:
            vol = float(vol)
        except ValueError:
            print('Команда remove_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                        f'Значение громкости должно быть числом.',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        if vol is None:
            embed = discord.Embed(title="", description=f"🔊 **{(vc.source.volume) * 100}%**",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        if not 0 < vol < 101:
            print("Введено значение неккоректное значение громкости")
            embed = discord.Embed(title="**Error**", description="Пожалуйста, введите значение от 1 до 100",
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

        player = self.get_player(ctx)

        if vc.source:
            vc.source.volume = vol / 100

        player.volume = vol / 100
        embed = discord.Embed(title="", description=f'**`{ctx.author}`** set the volume to **{vol}%**',
                              color=discord.Color.gold())
        await ctx.send(embed=embed)
        print(f'**`{ctx.author}`** set the volume to **{vol}%**')

    '''Команда. Кик бота из гс канала'''
    @commands.command(name='leave', aliases=['dc', 'disconnect', 'bye', 'discon'], description="stops music and disconnects from voice")
    async def leave_(self, ctx):
        """Убирает бота из голосового канала."""
        """!Warning!
            This will destroy the player assigned to your guild, also deleting any queued songs and settings.
        """
        print(f'Command leave_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        channel = ctx.author.voice.channel
        if not await self.check_connection(ctx, vc):
            return

        if (random.randint(0, 1) == 0):
            await ctx.message.add_reaction('👋')
        await ctx.send('**Successfully disconnected**')
        print(f'Бот был убран из канала "{channel}"')

        await self.cleanup(ctx.guild)

    '''В разработке. Команда. Установка песни на режим повторения'''
    '''@commands.command(name='repeat', aliases=['rep'], description="repeat song")
    async def repeat_(self, ctx, parameters: str = None):
        """**В разработке**. Повторяет песню заново"""
        print(f'Command repeat_ used by  {ctx.author.name}#{ctx.author.discriminator}')

        vc = ctx.voice_client
        player = self.get_player(ctx)

        if not await self.check_connection(ctx, vc):
            return
        if vc.is_paused():
            pass
        elif not await self.check_playing(ctx, player):
            return
        if parameters == None:
            raise commands.CommandInvokeError
        else:
            try:
                param = int(parameters)
            except ValueError:
                print('Команда repeat_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                            f'Укажите число повторений трека либо поставьте его на бесконечное повторение написав 0.',
                                      color = discord.Color.gold())
                return await ctx.send(embed=embed)
            if param < 0:
                print('Команда repeat_ введена неккоректно')
                embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                            f'Число повторений трека должно быть натуральным числом.',
                                      color=discord.Color.gold())
                return await ctx.send(embed=embed)
        if param == 0:
            self.repeater = True
        else:
            self.repeater = True
            self.repeaterNum = param
        print(f"Was repeat {vc.source.title}({vc.source.web_url}) by  {ctx.author.name}#{ctx.author.discriminator}")

        # vc.stop()
    '''

    '''Команда. Вывод истории всех проигранных треков'''
    @commands.command(name='history', aliases=['h', 'hyi', 'his', 'story'], description="history song")
    async def history_(self, ctx):
        """Показывает историю проигранных треков"""
        print(f'Command history_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client
        #print("History tracks:\n"+'\t\t'+ str(self.history_of_tracks))
        history =f'\n'.join(
                 f"`{(self.history_of_tracks.index(_)) + 1}.` [{_.title_name}] ` Requested by: {_.requester}`\n"
            for _ in self.history_of_tracks)
        history =f"\n" + history + f"\n**{len(self.history_of_tracks)} songs in history**"
        embed = discord.Embed(title=f'History track for {ctx.guild.name}', description=history, color=discord.Color.gold())
        embed.set_footer(text=f"{ctx.author.display_name}", icon_url=ctx.author.avatar_url)

        await ctx.send(embed=embed)

    '''В разработке. Команда. Воспроизведение трека с начала'''
    @commands.command(name='restart', aliases=["rest"], description="restart playing song")
    async def restart_(self, ctx):
        """**В разработке**.Заново воспроизводит трек"""
        print(f'Command restart_ used by  {ctx.author.name}#{ctx.author.discriminator}')
        vc = ctx.voice_client

        if not await self.check_connection(ctx, vc):
            return

        player = self.get_player(ctx)
        if not await self.check_playing(ctx, player):
            return
        self.repeater = True
        source = vc.source.title_name
        vc.stop()
        await self.playF_(ctx, source)

    '''Далее следует список обрабатываемых ошибок при вызове команд'''

    '''Обработка ошибки от команды playF_'''
    @playF_.error
    async def playF_error(self, ctx, error):
        if isinstance(error, commands.MissingRequiredArgument):
            print('Команда playF_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно, используйте синтаксис.\n'
                                                        f'{cod_bot.config.prefix}playF file name',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''Обработка ошибки от команды connect_'''
    @connect_.error
    async def connect_error(self, ctx, error):
        if isinstance(error, commands.ChannelNotFound):
            print('Команда connect_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                        f'Вы должны ввести существующее название голосового канала.',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''Обработка ошибки от команды remove_'''
    @remove_.error
    async def remove_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            print('Команда remove_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                        f'Укажите номер трека в очереди.',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''Обработка ошибки от команды change_volume'''
    @change_volume.error
    async def change_volume_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            print('Команда change_volume введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно, используйте синтаксис.\n'
                                                        f'{cod_bot.config.prefix}volume <integer>',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)

    '''Обработка ошибки от команды repeat_'''
    '''@repeat_.error
    async def repeat_error(self, ctx, error):
        if isinstance(error, commands.CommandInvokeError):
            print('Команда repeat_ введена неккоректно')
            embed = discord.Embed(title="**Error**", description=f'Команда введена неккоректно.\n'
                                                        f'Укажите число повторений.',
                                  color=discord.Color.gold())
            return await ctx.send(embed=embed)
    '''

'''Запуск бота и выбор его конфигурации'''
def setup(bot):
    bot.add_cog(Events(bot))
    if configuration == 'FileBot':
        bot.add_cog(MusicF(bot))
    else:
        bot.add_cog(Music(bot))

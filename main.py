import sys

'''Импортирование дополнительных библиотек'''
sys.path.append('venv\Lib\site-packages')
try:
    from discord.ext import commands
except Exception as e:
    print('Can not import files sa:' + str(e))
    input("Press Enter to exit!")
    sys.exit(0)

'''Импортирование папки с реализацией бота и его токеном и префиксом'''
sys.path.append('cod_bot')
try:
    from cod_bot.config import token
    from cod_bot.config import prefix
except Exception as e:
    print('Can not import files:' + str(e))
    input("Press Enter to exit!")
    sys.exit(0)

'''Установка префикса бота и подключение его реализации(после недавнего обновления используются упоминания)'''
bot = commands.Bot(command_prefix=commands.when_mentioned_or("!"))
bot.load_extension("cod_bot.bot")

'''Запуск бота'''
bot.run(token)



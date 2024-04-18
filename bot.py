import asyncio
import enum
import json
import pymysql
import os
from avito import Avito
from avito.schema.messenger.methods import SendMessage
from avito.schema.messenger.models import MessageToSend, WebhookMessage
from yandexgpt import YandexGPT
from config import host, user, password, db_name
from loguru import logger

class ProfileStatesGroup(enum.Enum):
    chat_begin = 1

    get_rent_date = 6
    confirm_rent_date = 7
    get_rent_people_number = 8
    get_user_contact = 9
    confirm_rent = 10
    waiting_3_days_before_invite = 11

    user_off_assistent = 20



class HotelBot:
    def __init__(self, avito: Avito, yandexgpt: YandexGPT):
        self.avito = avito
        self.yandexgpt = yandexgpt
        self.bot_message: str | None = "Не могу понять, что вы хотите. Попробуйте сформулировать иначе"
        self.database_connection = None
        self.message_from_user: str | None = None

    def __del__(self):
        self.database_connection.close()

    # Этот костыль-декоратор нужен чтобы решать выполянть определнное действие пользователя (запускать функцию или нет) взависимости от состояния пользователя и его команды
    def message_handler(command, state=None):
        def inner_decorator(func):
            def wrapped(*args, **kwargs):
                user_command = kwargs['command']
                user_state = kwargs['state']
                user_id = kwargs['user_id']
                if state:
                    if state != user_state:
                        return
                    elif user_command.find(command) != -1:
                        func(*args, command=user_command, state=user_state, user_id=user_id)
                elif user_command.find(command) != -1:
                    func(*args, command=user_command, state=user_state, user_id=user_id)

            return wrapped

        return inner_decorator

    async def process_message(self, message: WebhookMessage):
        message_text = message.content.text
        self.message_from_user = message_text
        state = self.get_user_chat_position(user_id=message.author_id)

        if state == None:
            with open('messages.json', 'r', encoding='utf-8') as file:
                messages = json.load(file)
            bot_message = messages['greetings']['welcome_message_for_new_user'].replace('\\n', '\n')
            self.set_user_chat_position(user_id=message.author_id, chat_position=ProfileStatesGroup.chat_begin.name)
            self.send_bot_message(message_from_webhook=message, read_chat=True, bot_message=bot_message)
            return
        else:
            user_action = self.define_user_action(message_from_user=message_text)
            self.start_pooling(message_from_user=user_action, state=state, user_id=message.author_id)
            if state != ProfileStatesGroup.user_off_assistent.name:
                await self.send_bot_message(message_from_webhook=message, read_chat=True)

    @message_handler(state=ProfileStatesGroup.user_off_assistent.name, command='Включить ассистента')
    def start_assistent(self, command, state, user_id):
        self.bot_message = "Ассистент активирован!"
        self.set_user_chat_position(user_id=user_id,
                                    chat_position=ProfileStatesGroup.chat_begin.name)
        return

    @message_handler(command='Отключить ассистента')
    def off_assisstent(self, command, state, user_id):
        self.bot_message = "Асситент отключен! Чат только с владельцем. Включить ассистента команда (\"/assistent_on\") или сообщение по типу: Включить ассистента"
        self.set_user_chat_position(user_id=user_id,
                                    chat_position=ProfileStatesGroup.user_off_assistent.name)
        return

    @message_handler(command='Сбросить состояние ассистента')
    def reset_asisstent(self, command, state, user_id):
        self.bot_message = "Ассистент сброшен!"
        self.set_user_chat_position(user_id=user_id,
                                    chat_position=ProfileStatesGroup.chat_begin.name)
        return

    @message_handler(command='Создать бронирование', state=ProfileStatesGroup.chat_begin.name)
    def create_new_booking(self, command, state, user_id):
        self.bot_message = "Создание нового бронирования: \nКакой период вас интересует?"
        self.set_user_chat_position(user_id=user_id,
                                                chat_position=ProfileStatesGroup.get_rent_date.name)
        return
    @message_handler(command="None", state=ProfileStatesGroup.get_rent_date.name)
    def get_rent_date(self, command, state, user_id):
        date = self.define_user_rent_date(message_from_user=self.message_from_user)
        if date != '0':
            self.bot_message = "Вас интересует период:{period}?".format(period=date)
            self.set_user_chat_position(user_id=user_id,
                                        chat_position=ProfileStatesGroup.confirm_rent_date.name)
        else:
            self.bot_message = "Не могу понять нужный вам период попробуйте ввести по другому."
        return

    @message_handler(command="None", state=ProfileStatesGroup.confirm_rent_date.name)
    def confirm_rent_date(self, command, state, user_id):
        confirm = self.define_user_confirm(message_from_user=self.message_from_user)
        if confirm != '0':
            if confirm.find('Да') != -1:
                self.bot_message = "Сейчас проверю есть ли свободные номера на данную дату..."
                pass
                self.set_user_chat_position(user_id=user_id,
                                        chat_position=ProfileStatesGroup.confirm_rent_date.name)
            if confirm.find('Нет') != -1:
                self.bot_message = "Введите нужную вам дату. Если я распознаю ее не правильно введите ее по другому."
                self.set_user_chat_position(user_id=user_id,
                                            chat_position=ProfileStatesGroup.get_rent_date.name)
        else:
            self.bot_message = "Не могу понять ваш ответ. Сформулируйте, точнее"
        return

    def start_pooling(self, message_from_user, state, user_id):
        self.start_assistent(command=message_from_user, state=state, user_id=user_id)
        self.off_assisstent(command=message_from_user, state=state, user_id=user_id)
        self.reset_asisstent(command=message_from_user, state=state, user_id=user_id)
        self.create_new_booking(command=message_from_user, state=state, user_id=user_id)
        self.get_rent_date(command='None', message_from_user=message_from_user, state=state, user_id=user_id)
        self.confirm_rent_date(command='None', message_from_user=message_from_user, state=state, user_id=user_id)

    def get_user_chat_position(self, user_id: str):
        if user_id is not None:
            try:
                with self.database_connection.cursor() as cursor:
                    select_user_chat_position = "SELECT chat_position FROM user_chat_position WHERE user_id = {user_id}".format(
                        user_id=user_id)
                    cursor.execute(select_user_chat_position)
                    rows = cursor.fetchall()

                    return rows[0]['chat_position'] if rows is not None else None
            except Exception as ex:
                logger.error(ex)

    def set_user_chat_position(self, user_id: str, chat_position: str):
        if user_id is not None and chat_position is not None:
            try:
                with self.database_connection.cursor() as cursor:
                    set_user_chat_position = "INSERT INTO user_chat_position (user_id, chat_position) VALUES ({user_id}, '{chat_position}') ON DUPLICATE KEY UPDATE chat_position = '{chat_position}'".format(
                        user_id=user_id, chat_position=chat_position)
                    cursor.execute(set_user_chat_position)
                    self.database_connection.commit()
            except Exception as ex:
                logger.error(ex)

    async def connect_database(self):
        try:
            logger.info("Trying connect to database...")
            connection = pymysql.connect(
                host=host,
                port=3306,
                user=user,
                password=password,
                database=db_name,
                cursorclass=pymysql.cursors.DictCursor
            )
            logger.info("Successfully connected!")
            self.database_connection = connection
        except Exception as ex:
            logger.error("Connection refused...")
            logger.error(ex)

    async def send_bot_message(self, message_from_webhook, read_chat: bool):
        # Чтение чата
        if read_chat:
            chat_read = message_from_webhook.read_message_chat()
            await self.avito.read_chat(chat_read)
        #
        # Отправка сообщения.
        await self.avito.send_message(message_from_webhook.answer(self.bot_message))

    def define_user_action(self, message_from_user: str):
        with open('messages.json', 'r', encoding='utf-8') as file:
            messages = json.load(file)
        promt = messages['yandex_gpt']['define_user_action_promt']
        message = [
            {
                "role": "system",
                "text": promt
            },
            {
                "role": "user",
                "text": message_from_user
            }
        ]
        result = self.yandexgpt.make_request(message)
        return result

    def define_user_confirm(self, message_from_user: str):
        with open('messages.json', 'r', encoding='utf-8') as file:
            messages = json.load(file)
        promt = messages['yandex_gpt']['confirm_user_input']
        message = [
            {
                "role": "system",
                "text": promt
            },
            {
                "role": "user",
                "text": "Сообщение от пользователя:" + message_from_user
            }
        ]
        result = self.yandexgpt.make_request(message)
        return result

    def define_user_rent_date(self, message_from_user: str):
        with open('messages.json', 'r', encoding='utf-8') as file:
            messages = json.load(file)
        promt = messages['yandex_gpt']['define_rent_date_promt']
        message = [
            {
                "role": "system",
                "text": promt
            },
            {
                "role": "user",
                "text": "Сообщение пользователя - " + message_from_user
            }
        ]
        result = self.yandexgpt.make_request(message)
        return result

    def prepare_message(self, message: str):
        prepared_message = message.strip()
        return prepared_message



    # async def process_user_command(self, command: str, author_id: str):
    #     match command:            # Если пользователь ввел не команду, то передаем ее на распознавание yandexgpt
    #         case _:
    #             user_action = await self.define_user_action(message_from_user=command)
    #             if user_action.find("Создать бронирование")!= -1:
    #                 self.bot_message = "Создание нового бронирования: \nКакая дата вас интересует?"
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.get_rent_date.name)
    #
    #             elif user_action.find("Изменить бронирование")!= -1:
    #                 self.bot_message = "Редактирование бронирования: \nПоиск существующего бронирования..."
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.mod_rent.name)
    #             elif user_action.find("Удалить бронирование")!= -1:
    #                 self.bot_message = "Удалить бронирование: \nПоиск существующего бронирования..."
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.del_rent.name)
    #             elif user_action.find("Условия аренды")!= -1:
    #                 self.bot_message = "Условия аренды:"
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.chat_begin.name)
    #             elif user_action.find("Условия проживания")!= -1:
    #                 self.bot_message = "Условия проживания:"
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.ask_question.name)
    #             elif user_action.find("Построение маршрута")!= -1:
    #                 self.bot_message = "Построение маршрута: \n Откуда вы поедете?"
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.build_route.name)
    #             elif user_action.find("Отключить ассистента")!= -1:
    #                 self.bot_message = "Асситент отключен! Чат только с владельцем. Включить ассистента команда (\"/assistent_on\") или сообщение по типу: Включить ассистента"
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.user_off_assistent.name)
    #             elif user_action.find("Включить ассистента")!= -1:
    #                 self.bot_message = "Ассистент активирован!"
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.chat_begin.name)
    #             elif user_action.find("Сбросить состояние ассистента")!= -1:
    #                 self.bot_message = "Ассистент сброшен!"
    #                 await self.set_user_chat_position(user_id=author_id,
    #                                                   chat_position=ProfileStatesGroup.chat_begin.name)
    #             else:
    #                 self.bot_message = "Не могу понять, что вы хотите, попробуйте иначе сформулировать запрос."










"""Client profile changes; account authentication stays in the HTTP layer."""


def validate_age(age):
    if type(age) is not int or not 1 <= age <= 120:
        raise ValueError('Введите возраст от 1 до 120 лет.')


def update(db, client_id, data):
    import access
    db.execute('BEGIN IMMEDIATE')
    access.ensure_active(db,'client',client_id)
    name, age = data.get('name'), data.get('age')
    if not isinstance(name, str) or not 2 <= len(name.strip()) <= 100:
        raise ValueError('Имя должно содержать от 2 до 100 символов.')
    validate_age(age)
    db.execute('UPDATE clients SET name=? WHERE id=?', (name.strip(), client_id))
    db.execute('INSERT INTO client_details VALUES (?,?) ON CONFLICT(client_id) DO UPDATE SET age=excluded.age', (client_id, age))

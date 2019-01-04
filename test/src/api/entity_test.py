from datetime import datetime
from random import randint

from aid.test.api import ApiTest
from service.api_definition import REQUIRED, NOT_UNIQUE


class Test(ApiTest):
    
    def test_create_and_get(self):
        entity = self.obj.create_group()
        entity_id = self\
            .post("/membership/group", entity)\
            .expect(code=201, status="created", data=entity)\
            .get('data__group_id')

        self.assertTrue(entity_id)

        self.get(f"/membership/group/{entity_id}").expect(code=200, data=entity, data__group_id=entity_id)

    def test_can_not_create_with_empty_data(self):
        self.post("/membership/group", dict()).expect(code=422)
        self.post("/membership/group", dict(group_id=1, created_at='remove_me')).expect(code=422)

    def test_update(self):
        entity = self.api.create_group()
        entity_id = entity['group_id']

        self.assertIsNone(entity['updated_at'])

        data = self\
            .put(f"/membership/group/{entity_id}", dict(name='arne'))\
            .expect(code=200, data__name='arne', data__group_id=entity_id)\
            .data
        
        self.assertTrue(data['updated_at'] >= data['created_at'])

    def test_can_not_updated_using_empty_or_read_only_data(self):
        entity = self.api.create_group()
        entity_id = entity['group_id']

        self.put(f"/membership/group/{entity_id}", dict()).expect(code=422)
        self.put(f"/membership/group/{entity_id}", dict(group_id=1, deleted_at='remove_me')).expect(code=422)

    def test_list(self):
        before = self.get("/membership/group").get('data')
        
        entity1_id = self.api.create_group()['group_id']
        entity2_id = self.api.create_group()['group_id']

        ids_before = {e['group_id'] for e in before}
        self.assertNotIn(entity1_id, ids_before)
        self.assertNotIn(entity2_id, ids_before)

        after = self.get("/membership/group").get('data')

        ids_after = {e['group_id'] for e in after}
        self.assertIn(entity1_id, ids_after)
        self.assertIn(entity2_id, ids_after)

    def test_deleted_entity_does_not_show_up_in_list(self):
        entity_id = self.api.create_group()['group_id']
        
        self.assertIn(entity_id, [e['group_id'] for e in self.get("/membership/group").data])
        
        self.delete(f"/membership/group/{entity_id}").expect(code=200, status="deleted")
        
        self.assertNotIn(entity_id, [e['group_id'] for e in self.get("/membership/group").data])

        data = self.get(f"/membership/group/{entity_id}").expect(code=200, data__group_id=entity_id).data
        
        self.assertTrue(data['deleted_at'] >= data['created_at'])

    def test_primary_key__created_at_and_updated_at_is_filtered_on_update(self):
        entity = self.api.create_group()
        entity_id = entity['group_id']

        t = datetime(2017, 1, 1).isoformat()

        data = self\
            .put(f"/membership/group/{entity_id}", dict(name='new_name', group_id=entity_id + 1,
                                                        created_at=t, updated_at=t))\
            .expect(code=200).data
        
        self.assertTrue(datetime.fromisoformat(data['updated_at']).year > 2017)
        self.assertTrue(datetime.fromisoformat(data['created_at']).year > 2017)
        self.assertEqual(entity_id, data['group_id'])
        self.assertEqual('new_name', data['name'])

        self.get(f"/membership/group/{entity_id}").expect(code=200, data__group_id=entity_id)

    def test_unique_constraint_fails_with_message(self):
        entity_1 = self.obj.create_member(member_number=randint(1e8, 9e8))
        email = entity_1['email']
        entity_2 = self.obj.create_member(member_number=randint(1e8, 9e8), email=email)
        
        self.post("/membership/member", entity_1).expect(code=201)
        self.post("/membership/member", entity_2).expect(code=422,
                                                         what=NOT_UNIQUE,
                                                         message=f"'{email}' already exists.")
        
    def test_not_null_constraint_fails_with_message(self):
        member = self.obj.create_member()
        member.pop('member_number', None)
        
        self.post("/membership/member", member).expect(code=422, what=REQUIRED, fields='member_number')
        
    # TODO Test filtering.
    # TODO Test pagination.
    